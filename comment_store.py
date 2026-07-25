"""SQLite persistence for fetched YouTube comments."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fetch_comments import Comment

COMMENT_FIELDS = (
    "thread_id",
    "video_id",
    "author_display_name",
    "author_channel_id",
    "text",
    "like_count",
    "published_at",
    "updated_at",
    "total_reply_count",
    "can_reply",
    "is_public",
)

INBOX_COLUMNS = {
    "status": "TEXT NOT NULL DEFAULT 'new'",
    "priority": "TEXT",
    "category": "TEXT",
    "classification_reason": "TEXT",
    "reply_worthy": "INTEGER",
    "classified_at": "TEXT",
    "classification_model": "TEXT",
    "classification_version": "TEXT",
    "draft_reply": "TEXT",
    "final_reply": "TEXT",
    "needs_research": "INTEGER NOT NULL DEFAULT 0",
    "is_ignored": "INTEGER NOT NULL DEFAULT 0",
    "replied_at": "TEXT",
    "has_creator_reply": "INTEGER NOT NULL DEFAULT 0",
    "creator_reply_id": "TEXT",
    "creator_replied_at": "TEXT",
    "reply_status_checked_at": "TEXT",
    "reply_approved_at": "TEXT",
}


@dataclass(frozen=True)
class SyncSummary:
    fetched: int
    newly_inserted: int
    updated: int
    unchanged: int


def connect_database(database_path: str) -> sqlite3.Connection:
    """Open the configured SQLite database."""
    path = Path(database_path)
    if path.parent != Path("."):
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def create_comments_table(connection: sqlite3.Connection) -> None:
    """Create the comments table when it does not already exist."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS comments (
            comment_id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            video_id TEXT NOT NULL,
            author_display_name TEXT NOT NULL,
            author_channel_id TEXT,
            text TEXT NOT NULL,
            like_count INTEGER NOT NULL,
            published_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            total_reply_count INTEGER NOT NULL,
            can_reply INTEGER NOT NULL,
            is_public INTEGER NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            priority TEXT,
            category TEXT,
            classification_reason TEXT,
            reply_worthy INTEGER,
            classified_at TEXT,
            classification_model TEXT,
            classification_version TEXT,
            draft_reply TEXT,
            final_reply TEXT,
            needs_research INTEGER NOT NULL DEFAULT 0,
            is_ignored INTEGER NOT NULL DEFAULT 0,
            replied_at TEXT,
            has_creator_reply INTEGER NOT NULL DEFAULT 0,
            creator_reply_id TEXT,
            creator_replied_at TEXT,
            reply_status_checked_at TEXT
            , reply_approved_at TEXT
        )
        """
    )
    existing_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(comments)")
    }
    for column_name, definition in INBOX_COLUMNS.items():
        if column_name not in existing_columns:
            connection.execute(
                f"ALTER TABLE comments ADD COLUMN {column_name} {definition}"
            )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sync_comments(
    connection: sqlite3.Connection,
    comments: list[Comment],
    synced_at: str | None = None,
) -> SyncSummary:
    """Upsert comments and report how the stored records changed."""
    observed_at = synced_at or utc_now()
    unique_comments = {comment.comment_id: comment for comment in comments}
    inserted = 0
    updated = 0
    unchanged = 0

    create_comments_table(connection)
    with connection:
        for comment in unique_comments.values():
            values = asdict(comment)
            existing = connection.execute(
                f"SELECT {', '.join(COMMENT_FIELDS)} "
                "FROM comments WHERE comment_id = ?",
                (comment.comment_id,),
            ).fetchone()

            comparable_values = tuple(
                int(values[field]) if field in {"can_reply", "is_public"} else values[field]
                for field in COMMENT_FIELDS
            )
            if existing is None:
                inserted += 1
            elif tuple(existing[field] for field in COMMENT_FIELDS) == comparable_values:
                unchanged += 1
            else:
                updated += 1

            connection.execute(
                """
                INSERT INTO comments (
                    comment_id, thread_id, video_id, author_display_name,
                    author_channel_id, text, like_count, published_at, updated_at,
                    total_reply_count, can_reply, is_public, first_seen_at,
                    last_seen_at
                ) VALUES (
                    :comment_id, :thread_id, :video_id, :author_display_name,
                    :author_channel_id, :text, :like_count, :published_at,
                    :updated_at, :total_reply_count, :can_reply, :is_public,
                    :first_seen_at, :last_seen_at
                )
                ON CONFLICT(comment_id) DO UPDATE SET
                    thread_id = excluded.thread_id,
                    video_id = excluded.video_id,
                    author_display_name = excluded.author_display_name,
                    author_channel_id = excluded.author_channel_id,
                    text = excluded.text,
                    like_count = excluded.like_count,
                    published_at = excluded.published_at,
                    updated_at = excluded.updated_at,
                    total_reply_count = excluded.total_reply_count,
                    can_reply = excluded.can_reply,
                    is_public = excluded.is_public,
                    last_seen_at = excluded.last_seen_at
                """,
                {
                    **values,
                    "can_reply": int(comment.can_reply),
                    "is_public": int(comment.is_public),
                    "first_seen_at": observed_at,
                    "last_seen_at": observed_at,
                },
            )

    return SyncSummary(
        fetched=len(unique_comments),
        newly_inserted=inserted,
        updated=updated,
        unchanged=unchanged,
    )
