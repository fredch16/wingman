"""Persistent keyword automations for pre-filling creator replies."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from wingman.db.video_catalog import create_videos_table, utc_now


@dataclass(frozen=True)
class Automation:
    automation_id: int
    video_id: str
    video_title: str
    thumbnail_url: str
    platform: str
    keywords: tuple[str, ...]
    default_reply: str
    is_enabled: bool
    created_at: str
    updated_at: str
    mode: str = "prefill"
    initial_dm: str = ""
    followup_dm: str = ""
    match_type: str = "contains"


@dataclass(frozen=True)
class AutomationMatch:
    automation_id: int
    matched_keyword: str
    default_reply: str


def normalize_keywords(value: str | list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Keep explicit case variations while removing whitespace and duplicates."""
    values = value.split(",") if isinstance(value, str) else value
    normalized: list[str] = []
    for value_item in values:
        keyword = " ".join(value_item.split()).strip()
        if keyword and keyword not in normalized:
            normalized.append(keyword)
    return tuple(normalized)


class AutomationRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        create_videos_table(connection)
        self.create_table()

    def create_table(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS automations (
                automation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT NOT NULL,
                keywords TEXT NOT NULL,
                default_reply TEXT NOT NULL,
                is_enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(video_id) REFERENCES videos(video_id)
            )
            """
        )
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(automations)")}
        for name in ("mode", "initial_dm", "followup_dm", "match_type"):
            if name not in columns:
                self.connection.execute(
                    f"ALTER TABLE automations ADD COLUMN {name} TEXT NOT NULL DEFAULT ''"
                )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS automation_deliveries (
                comment_id TEXT PRIMARY KEY,
                automation_id INTEGER NOT NULL,
                sender_id TEXT NOT NULL DEFAULT '',
                recipient_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                private_message_id TEXT NOT NULL DEFAULT '',
                public_reply_id TEXT NOT NULL DEFAULT '',
                followup_message_id TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS automations_video_enabled
            ON automations(video_id, is_enabled, automation_id)
            """
        )

    def create(
        self,
        video_id: str,
        keywords: str | list[str] | tuple[str, ...],
        default_reply: str,
        *,
        mode: str = "prefill",
        initial_dm: str = "",
        followup_dm: str = "",
        match_type: str = "contains",
    ) -> int:
        parsed_keywords = normalize_keywords(keywords)
        reply = default_reply.strip()
        if not parsed_keywords:
            raise ValueError("Add at least one keyword.")
        if not reply:
            raise ValueError("Add a default response.")
        if not self._video_exists(video_id):
            raise ValueError("Select a stored video.")
        self._validate_delivery(mode, initial_dm, followup_dm, video_id, match_type)
        timestamp = utc_now()
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO automations (
                    video_id, keywords, default_reply, is_enabled,
                    created_at, updated_at, mode, initial_dm, followup_dm, match_type
                ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
                """,
                (
                    video_id,
                    json.dumps(parsed_keywords, ensure_ascii=False),
                    reply,
                    timestamp,
                    timestamp,
                    mode,
                    initial_dm.strip(),
                    followup_dm.strip(),
                    match_type,
                ),
            )
        return int(cursor.lastrowid)

    def update(
        self,
        automation_id: int,
        keywords: str | list[str] | tuple[str, ...],
        default_reply: str,
        *,
        mode: str = "prefill",
        initial_dm: str = "",
        followup_dm: str = "",
        match_type: str = "contains",
    ) -> bool:
        parsed_keywords = normalize_keywords(keywords)
        reply = default_reply.strip()
        if not parsed_keywords:
            raise ValueError("Add at least one keyword.")
        if not reply:
            raise ValueError("Add a default response.")
        row = self.connection.execute("SELECT video_id FROM automations WHERE automation_id = ?", (automation_id,)).fetchone()
        if row is None:
            return False
        self._validate_delivery(mode, initial_dm, followup_dm, row["video_id"], match_type)
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE automations
                SET keywords = ?, default_reply = ?, updated_at = ?,
                    mode = ?, initial_dm = ?, followup_dm = ?, match_type = ?
                WHERE automation_id = ?
                """,
                (
                    json.dumps(parsed_keywords, ensure_ascii=False),
                    reply,
                    utc_now(),
                    mode,
                    initial_dm.strip(),
                    followup_dm.strip(),
                    match_type,
                    automation_id,
                ),
            )
        return cursor.rowcount > 0

    def set_enabled(self, automation_id: int, is_enabled: bool) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE automations
                SET is_enabled = ?, updated_at = ?
                WHERE automation_id = ?
                """,
                (int(is_enabled), utc_now(), automation_id),
            )
        return cursor.rowcount > 0

    def delete(self, automation_id: int) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                "DELETE FROM automations WHERE automation_id = ?",
                (automation_id,),
            )
        return cursor.rowcount > 0

    def list_all(self) -> list[Automation]:
        rows = self.connection.execute(
            """
            SELECT automations.automation_id, automations.video_id,
                   COALESCE(videos.title, automations.video_id) AS video_title,
                   COALESCE(videos.thumbnail_url, '') AS thumbnail_url,
                   COALESCE(videos.platform, 'youtube') AS platform,
                   automations.keywords, automations.default_reply,
                   automations.is_enabled, automations.created_at,
                   automations.updated_at, automations.mode,
                   automations.initial_dm, automations.followup_dm,
                   automations.match_type
            FROM automations
            LEFT JOIN videos ON videos.video_id = automations.video_id
            ORDER BY automations.created_at DESC, automations.automation_id DESC
            """
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def match(self, video_id: str, comment_text: str) -> AutomationMatch | None:
        rows = self.connection.execute(
            """
            SELECT automation_id, keywords, default_reply
            FROM automations
            WHERE video_id = ? AND is_enabled = 1 AND (mode = 'prefill' OR mode = '')
            ORDER BY automation_id
            """,
            (video_id,),
        ).fetchall()
        for row in rows:
            keywords = self._decode_keywords(row["keywords"])
            for keyword in keywords:
                if keyword in comment_text:
                    return AutomationMatch(
                        automation_id=row["automation_id"],
                        matched_keyword=keyword,
                        default_reply=row["default_reply"],
                    )
        return None

    def get(self, automation_id: int) -> Automation | None:
        return next((item for item in self.list_all() if item.automation_id == automation_id), None)

    def match_delivery(self, automation: Automation, comment_text: str) -> str | None:
        if automation.mode != "instagram_dm" or not automation.is_enabled:
            return None
        if automation.match_type == "exact":
            return next((word for word in automation.keywords if word == comment_text.strip()), None)
        return next((word for word in automation.keywords if word.casefold() in comment_text.casefold()), None)

    def delivery(self, comment_id: str) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM automation_deliveries WHERE comment_id = ?", (comment_id,)).fetchone()

    def claim_delivery(self, comment_id: str, automation_id: int, sender_id: str) -> bool:
        now = utc_now()
        with self.connection:
            cursor = self.connection.execute(
                """INSERT OR IGNORE INTO automation_deliveries
                   (comment_id, automation_id, sender_id, status, created_at, updated_at)
                   VALUES (?, ?, ?, 'sending', ?, ?)""",
                (comment_id, automation_id, sender_id, now, now),
            )
        return cursor.rowcount == 1

    def update_delivery(self, comment_id: str, status: str, **fields: str) -> None:
        allowed = {"recipient_id", "private_message_id", "public_reply_id", "followup_message_id", "error"}
        if set(fields) - allowed:
            raise ValueError("Unknown delivery field")
        assignments = ", ".join(["status = ?", "updated_at = ?"] + [f"{name} = ?" for name in fields])
        with self.connection:
            self.connection.execute(
                f"UPDATE automation_deliveries SET {assignments} WHERE comment_id = ?",
                (status, utc_now(), *fields.values(), comment_id),
            )

    def claim_followup(self, comment_id: str, sender_id: str) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                """UPDATE automation_deliveries SET status = 'followup_sending', updated_at = ?
                   WHERE comment_id = ? AND recipient_id = ? AND status IN ('awaiting_opt_in', 'public_failed')""",
                (utc_now(), comment_id, sender_id),
            )
        return cursor.rowcount == 1

    def _validate_delivery(self, mode: str, initial_dm: str, followup_dm: str, video_id: str, match_type: str) -> None:
        if mode not in {"prefill", "instagram_dm"}:
            raise ValueError("Unknown automation mode.")
        if match_type not in {"contains", "exact"}:
            raise ValueError("Unknown matching type.")
        if mode == "instagram_dm":
            platform = self.connection.execute("SELECT platform FROM videos WHERE video_id = ?", (video_id,)).fetchone()
            if not platform or platform["platform"] != "instagram":
                raise ValueError("DM automation requires an Instagram post or Reel.")
            if not initial_dm.strip() or not followup_dm.strip():
                raise ValueError("Add both the opening DM and the follow-up message.")

    def _video_exists(self, video_id: str) -> bool:
        return (
            self.connection.execute(
                "SELECT 1 FROM videos WHERE video_id = ?", (video_id,)
            ).fetchone()
            is not None
        )

    @staticmethod
    def _decode_keywords(value: str) -> tuple[str, ...]:
        decoded = json.loads(value)
        return normalize_keywords(tuple(str(item) for item in decoded))

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> Automation:
        return Automation(
            automation_id=row["automation_id"],
            video_id=row["video_id"],
            video_title=row["video_title"],
            thumbnail_url=row["thumbnail_url"],
            platform=row["platform"],
            keywords=cls._decode_keywords(row["keywords"]),
            default_reply=row["default_reply"],
            is_enabled=bool(row["is_enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            mode=row["mode"] or "prefill",
            initial_dm=row["initial_dm"],
            followup_dm=row["followup_dm"],
            match_type=row["match_type"] or "contains",
        )
