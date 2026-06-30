import sqlite3
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    youtube_comment_id TEXT UNIQUE NOT NULL,
    youtube_thread_id TEXT,
    video_id TEXT,
    video_title TEXT,
    author_name TEXT,
    author_channel_id TEXT,
    text TEXT,
    like_count INTEGER,
    published_at TEXT,
    updated_at TEXT,
    fetched_at TEXT,
    status TEXT DEFAULT 'synced'
);
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(database_path: str) -> sqlite3.Connection:
    """Open the SQLite database and return a connection."""
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
    """Create database tables if they do not already exist."""
    connection.execute(SCHEMA)
    connection.commit()


def upsert_comment(connection: sqlite3.Connection, comment: dict) -> None:
    """Insert a comment, or update fields that can change on later syncs."""
    fetched_at = utc_now_iso()

    connection.execute(
        """
        INSERT INTO comments (
            youtube_comment_id,
            youtube_thread_id,
            video_id,
            video_title,
            author_name,
            author_channel_id,
            text,
            like_count,
            published_at,
            updated_at,
            fetched_at,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'synced')
        ON CONFLICT(youtube_comment_id) DO UPDATE SET
            youtube_thread_id = excluded.youtube_thread_id,
            video_id = excluded.video_id,
            video_title = excluded.video_title,
            author_name = excluded.author_name,
            author_channel_id = excluded.author_channel_id,
            text = excluded.text,
            like_count = excluded.like_count,
            updated_at = excluded.updated_at,
            fetched_at = excluded.fetched_at,
            status = 'synced'
        """,
        (
            comment["youtube_comment_id"],
            comment.get("youtube_thread_id"),
            comment.get("video_id"),
            comment.get("video_title"),
            comment.get("author_name"),
            comment.get("author_channel_id"),
            comment.get("text"),
            comment.get("like_count"),
            comment.get("published_at"),
            comment.get("updated_at"),
            fetched_at,
        ),
    )


def count_comments(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT COUNT(*) AS total FROM comments").fetchone()
    return int(row["total"])
