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
    ) -> int:
        parsed_keywords = normalize_keywords(keywords)
        reply = default_reply.strip()
        if not parsed_keywords:
            raise ValueError("Add at least one keyword.")
        if not reply:
            raise ValueError("Add a default response.")
        if not self._video_exists(video_id):
            raise ValueError("Select a stored video.")
        timestamp = utc_now()
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO automations (
                    video_id, keywords, default_reply, is_enabled,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 1, ?, ?)
                """,
                (
                    video_id,
                    json.dumps(parsed_keywords, ensure_ascii=False),
                    reply,
                    timestamp,
                    timestamp,
                ),
            )
        return int(cursor.lastrowid)

    def update(
        self,
        automation_id: int,
        keywords: str | list[str] | tuple[str, ...],
        default_reply: str,
    ) -> bool:
        parsed_keywords = normalize_keywords(keywords)
        reply = default_reply.strip()
        if not parsed_keywords:
            raise ValueError("Add at least one keyword.")
        if not reply:
            raise ValueError("Add a default response.")
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE automations
                SET keywords = ?, default_reply = ?, updated_at = ?
                WHERE automation_id = ?
                """,
                (
                    json.dumps(parsed_keywords, ensure_ascii=False),
                    reply,
                    utc_now(),
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
                   automations.updated_at
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
            WHERE video_id = ? AND is_enabled = 1
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
        )
