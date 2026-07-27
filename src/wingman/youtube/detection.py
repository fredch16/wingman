"""Detect existing creator replies before inbox classification."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from wingman.db.comment_store import create_comments_table

CREATOR_CHANNEL_SETTING = "authenticated_creator_channel_id"
BACKLOG_CHECK_SETTING = "reply_backlog_last_checked_at"


@dataclass(frozen=True)
class ReplyCheckTarget:
    comment_id: str
    thread_id: str
    video_id: str
    total_reply_count: int


@dataclass(frozen=True)
class CreatorReply:
    reply_id: str
    published_at: str


@dataclass(frozen=True)
class ReplyCheckSummary:
    checked: int
    replied: int
    unreplied: int
    failed: int
    skipped: bool = False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def create_settings_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def store_creator_channel_id(
    connection: sqlite3.Connection, channel_id: str
) -> None:
    create_settings_table(connection)
    with connection:
        connection.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (CREATOR_CHANNEL_SETTING, channel_id, utc_now()),
        )


def get_stored_creator_channel_id(
    connection: sqlite3.Connection,
) -> str | None:
    create_settings_table(connection)
    row = connection.execute(
        "SELECT value FROM settings WHERE key = ?",
        (CREATOR_CHANNEL_SETTING,),
    ).fetchone()
    return row["value"] if row else None


def get_setting(connection: sqlite3.Connection, key: str) -> str | None:
    create_settings_table(connection)
    row = connection.execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else None


def store_setting(
    connection: sqlite3.Connection, key: str, value: str
) -> None:
    create_settings_table(connection)
    with connection:
        connection.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, utc_now()),
        )


def backlog_checked_recently(
    connection: sqlite3.Connection,
    now: datetime | None = None,
    freshness: timedelta = timedelta(hours=1),
) -> bool:
    value = get_setting(connection, BACKLOG_CHECK_SETTING)
    if not value:
        return False
    checked_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (now or datetime.now(timezone.utc)) - checked_at < freshness


def fetch_authenticated_creator_channel_id(youtube: Any) -> str:
    response = youtube.channels().list(part="id", mine=True).execute()
    items = response.get("items", [])
    if not items or not items[0].get("id"):
        raise RuntimeError("Could not determine the authenticated channel ID.")
    return items[0]["id"]


def comments_pending_reply_check(
    connection: sqlite3.Connection,
    video_ids: list[str] | None = None,
    include_checked_unreplied: bool = False,
) -> list[ReplyCheckTarget]:
    create_comments_table(connection)
    parameters: tuple[object, ...] = ()
    video_filter = ""
    if video_ids:
        placeholders = ", ".join("?" for _ in video_ids)
        video_filter = f" AND video_id IN ({placeholders})"
        parameters = tuple(video_ids)
    checked_filter = (
        "" if include_checked_unreplied else "AND reply_status_checked_at IS NULL"
    )
    rows = connection.execute(
        f"""
        SELECT comment_id, thread_id, video_id, total_reply_count
        FROM comments
        WHERE platform = 'youtube'
          AND has_creator_reply = 0
          {checked_filter}
        {video_filter}
        ORDER BY published_at, comment_id
        """,
        parameters,
    ).fetchall()
    return [
        ReplyCheckTarget(
            comment_id=row["comment_id"],
            thread_id=row["thread_id"],
            video_id=row["video_id"],
            total_reply_count=row["total_reply_count"],
        )
        for row in rows
    ]


def creator_reply_from_comments(
    replies: list[dict[str, Any]], creator_channel_id: str
) -> CreatorReply | None:
    for reply in replies:
        snippet = reply.get("snippet", {})
        author_channel = snippet.get("authorChannelId", {})
        if author_channel.get("value") == creator_channel_id:
            return CreatorReply(
                reply_id=reply["id"],
                published_at=snippet.get("publishedAt", ""),
            )
    return None


def fetch_all_replies(
    youtube: Any,
    parent_comment_id: str,
    verbose: bool = False,
) -> list[dict[str, Any]]:
    replies: list[dict[str, Any]] = []
    page_token: str | None = None
    seen_tokens: set[str] = set()
    page_number = 1
    while True:
        if verbose:
            print(
                f"    Fetching full replies page {page_number}...",
                flush=True,
            )
        response = (
            youtube.comments()
            .list(
                part="snippet",
                parentId=parent_comment_id,
                maxResults=100,
                textFormat="plainText",
                pageToken=page_token,
            )
            .execute()
        )
        replies.extend(response.get("items", []))
        next_page_token = response.get("nextPageToken")
        if not next_page_token:
            return replies
        if next_page_token in seen_tokens:
            raise RuntimeError("Repeated reply nextPageToken detected.")
        seen_tokens.add(next_page_token)
        page_token = next_page_token
        page_number += 1


def detect_creator_reply(
    youtube: Any,
    target: ReplyCheckTarget,
    creator_channel_id: str,
    verbose: bool = False,
) -> CreatorReply | None:
    """Inspect embedded replies, loading the complete reply list when needed."""
    if target.total_reply_count == 0:
        return None

    if verbose:
        print("    Fetching comment thread replies...", flush=True)
    response = (
        youtube.commentThreads()
        .list(
            part="snippet,replies",
            id=target.thread_id,
            textFormat="plainText",
        )
        .execute()
    )
    threads = response.get("items", [])
    if not threads:
        raise RuntimeError(f"Comment thread not found: {target.thread_id}")

    thread = threads[0]
    embedded_replies = thread.get("replies", {}).get("comments", [])
    creator_reply = creator_reply_from_comments(
        embedded_replies, creator_channel_id
    )
    if creator_reply:
        return creator_reply

    total_reply_count = thread.get("snippet", {}).get(
        "totalReplyCount", target.total_reply_count
    )
    if len(embedded_replies) < total_reply_count:
        if verbose:
            print(
                "    Embedded replies are incomplete; "
                "requesting the full reply list.",
                flush=True,
            )
        complete_replies = fetch_all_replies(
            youtube,
            target.comment_id,
            verbose=verbose,
        )
        return creator_reply_from_comments(complete_replies, creator_channel_id)
    return None


def persist_reply_check(
    connection: sqlite3.Connection,
    comment_id: str,
    creator_reply: CreatorReply | None,
    checked_at: str,
) -> None:
    with connection:
        connection.execute(
            """
            UPDATE comments SET
                has_creator_reply = ?,
                creator_reply_id = ?,
                creator_replied_at = ?,
                reply_status_checked_at = ?
            WHERE comment_id = ?
            """,
            (
                int(creator_reply is not None),
                creator_reply.reply_id if creator_reply else None,
                creator_reply.published_at if creator_reply else None,
                checked_at,
                comment_id,
            ),
        )


def backfill_reply_status(
    youtube: Any,
    connection: sqlite3.Connection,
    creator_channel_id: str,
    video_ids: list[str] | None = None,
    checked_at: str | None = None,
    refresh: bool = False,
    now: datetime | None = None,
) -> ReplyCheckSummary:
    """Check each unchecked comment once and persist successful results."""
    if not refresh and backlog_checked_recently(connection, now=now):
        return ReplyCheckSummary(0, 0, 0, 0, skipped=True)

    operation_checked_at = checked_at or utc_now()
    targets = comments_pending_reply_check(
        connection,
        video_ids,
        include_checked_unreplied=refresh,
    )
    replied = 0
    unreplied = 0
    failed = 0
    api_checks = sum(target.total_reply_count > 0 for target in targets)
    print(
        f"Reply backlog: {len(targets)} comments pending "
        f"({api_checks} require API checks).",
        flush=True,
    )
    if not targets:
        print("Reply backlog is already up to date.", flush=True)

    for index, target in enumerate(targets, start=1):
        print(
            f"[{index}/{len(targets)}] {target.comment_id} "
            f"({target.total_reply_count} replies)",
            flush=True,
        )
        try:
            creator_reply = detect_creator_reply(
                youtube,
                target,
                creator_channel_id,
                verbose=True,
            )
            persist_reply_check(
                connection,
                target.comment_id,
                creator_reply,
                operation_checked_at,
            )
            if creator_reply:
                replied += 1
                print(
                    f"    Creator reply found: {creator_reply.reply_id}",
                    flush=True,
                )
            else:
                unreplied += 1
                if target.total_reply_count == 0:
                    print("    No replies; no API call needed.", flush=True)
                else:
                    print("    No creator reply found.", flush=True)
        except Exception as error:
            failed += 1
            print(
                f"    Reply check failed: {error}",
                flush=True,
            )

    summary = ReplyCheckSummary(
        checked=replied + unreplied,
        replied=replied,
        unreplied=unreplied,
        failed=failed,
    )
    if not failed:
        store_setting(
            connection,
            BACKLOG_CHECK_SETTING,
            operation_checked_at,
        )
    return summary
