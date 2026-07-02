import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    youtube_comment_id TEXT UNIQUE NOT NULL,
    youtube_thread_id TEXT,
    parent_comment_id TEXT,
    is_reply INTEGER DEFAULT 0,
    video_id TEXT,
    video_title TEXT,
    author_name TEXT,
    author_channel_id TEXT,
    text TEXT,
    like_count INTEGER,
    published_at TEXT,
    updated_at TEXT,
    fetched_at TEXT,
    wingman_reply_text TEXT,
    youtube_reply_id TEXT,
    replied_at TEXT,
    skipped_until TEXT,
    last_seen_at TEXT,
    notes TEXT,
    ai_draft_text TEXT,
    ai_draft_model TEXT,
    ai_draft_provider TEXT,
    ai_drafted_at TEXT,
    status TEXT DEFAULT 'synced'
);
"""


MIGRATIONS = [
    ("parent_comment_id", "ALTER TABLE comments ADD COLUMN parent_comment_id TEXT"),
    ("is_reply", "ALTER TABLE comments ADD COLUMN is_reply INTEGER DEFAULT 0"),
    ("wingman_reply_text", "ALTER TABLE comments ADD COLUMN wingman_reply_text TEXT"),
    ("youtube_reply_id", "ALTER TABLE comments ADD COLUMN youtube_reply_id TEXT"),
    ("replied_at", "ALTER TABLE comments ADD COLUMN replied_at TEXT"),
    ("skipped_until", "ALTER TABLE comments ADD COLUMN skipped_until TEXT"),
    ("last_seen_at", "ALTER TABLE comments ADD COLUMN last_seen_at TEXT"),
    ("notes", "ALTER TABLE comments ADD COLUMN notes TEXT"),
    ("ai_draft_text", "ALTER TABLE comments ADD COLUMN ai_draft_text TEXT"),
    ("ai_draft_model", "ALTER TABLE comments ADD COLUMN ai_draft_model TEXT"),
    ("ai_draft_provider", "ALTER TABLE comments ADD COLUMN ai_draft_provider TEXT"),
    ("ai_drafted_at", "ALTER TABLE comments ADD COLUMN ai_drafted_at TEXT"),
]


ACTION_HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS action_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    comment_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    previous_status TEXT,
    previous_wingman_reply_text TEXT,
    previous_youtube_reply_id TEXT,
    previous_replied_at TEXT,
    previous_skipped_until TEXT,
    previous_last_seen_at TEXT,
    previous_notes TEXT,
    youtube_reply_id TEXT,
    created_at TEXT NOT NULL
);
"""


AI_DRAFTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    comment_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    provider TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    suggested_reply TEXT NOT NULL,
    status TEXT DEFAULT 'generated',
    created_at TEXT NOT NULL
);
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_future_iso(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def connect(database_path: str) -> sqlite3.Connection:
    """Open the SQLite database and return a connection."""
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
    """Create database tables if they do not already exist."""
    connection.execute(SCHEMA)
    connection.execute(ACTION_HISTORY_SCHEMA)
    connection.execute(AI_DRAFTS_SCHEMA)
    existing_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(comments)")
    }
    for column_name, statement in MIGRATIONS:
        if column_name not in existing_columns:
            connection.execute(statement)
    connection.commit()


def upsert_comment(connection: sqlite3.Connection, comment: dict) -> None:
    """Insert a comment, or update fields that can change on later syncs."""
    fetched_at = utc_now_iso()

    connection.execute(
        """
        INSERT INTO comments (
            youtube_comment_id,
            youtube_thread_id,
            parent_comment_id,
            is_reply,
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'synced')
        ON CONFLICT(youtube_comment_id) DO UPDATE SET
            youtube_thread_id = excluded.youtube_thread_id,
            parent_comment_id = excluded.parent_comment_id,
            is_reply = excluded.is_reply,
            video_id = excluded.video_id,
            video_title = excluded.video_title,
            author_name = excluded.author_name,
            author_channel_id = excluded.author_channel_id,
            text = excluded.text,
            like_count = excluded.like_count,
            updated_at = excluded.updated_at,
            fetched_at = excluded.fetched_at,
            status = CASE
                WHEN comments.status IN (
                    'replied',
                    'externally_replied',
                    'ignored',
                    'needs_research',
                    'skipped'
                ) THEN comments.status
                ELSE 'synced'
            END
        """,
        (
            comment["youtube_comment_id"],
            comment.get("youtube_thread_id"),
            comment.get("parent_comment_id"),
            1 if comment.get("is_reply") else 0,
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


def count_pending_top_level_comments(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        """
        SELECT COUNT(*) AS total
        FROM comments
        WHERE COALESCE(is_reply, 0) = 0
          AND (
              status = 'synced'
              OR (
                  status = 'skipped'
                  AND (
                      skipped_until IS NULL
                      OR datetime(skipped_until) <= datetime('now')
                  )
              )
          )
        """
    ).fetchone()
    return int(row["total"])


def get_queue_stats(connection: sqlite3.Connection) -> dict:
    """Return counts for the review dashboard."""
    rows = connection.execute(
        """
        SELECT status, COUNT(*) AS total
        FROM comments
        WHERE COALESCE(is_reply, 0) = 0
        GROUP BY status
        """
    ).fetchall()
    stats = {
        "pending": 0,
        "skipped": 0,
        "ignored": 0,
        "needs_research": 0,
        "externally_replied": 0,
        "replied": 0,
    }
    for row in rows:
        status = row["status"] or "synced"
        key = "pending" if status == "synced" else status
        if key in stats:
            stats[key] = int(row["total"])

    replied_today = connection.execute(
        """
        SELECT COUNT(*) AS total
        FROM comments
        WHERE COALESCE(is_reply, 0) = 0
          AND status = 'replied'
          AND date(replied_at) = date('now')
        """
    ).fetchone()
    stats["replied_today"] = int(replied_today["total"])
    return stats


def get_next_review_comment(
    connection: sqlite3.Connection,
    status_filter: str = "pending",
    search: str = "",
    offset: int = 0,
) -> sqlite3.Row | None:
    """Return the next top-level comment for the current review filter."""
    offset = max(offset, 0)
    clauses = ["COALESCE(is_reply, 0) = 0"]
    params = []

    if status_filter == "pending":
        clauses.append(
            """
            (
                status = 'synced'
                OR (
                    status = 'skipped'
                    AND (
                        skipped_until IS NULL
                        OR datetime(skipped_until) <= datetime('now')
                    )
                )
            )
            """
        )
    elif status_filter in {
        "skipped",
        "ignored",
        "needs_research",
        "externally_replied",
        "replied",
    }:
        clauses.append("status = ?")
        params.append(status_filter)
    else:
        clauses.append(
            """
            status NOT IN (
                'replied',
                'externally_replied',
                'ignored'
            )
            """
        )

    if search:
        clauses.append(
            """
            (
                text LIKE ?
                OR author_name LIKE ?
                OR video_title LIKE ?
                OR notes LIKE ?
            )
            """
        )
        like_search = f"%{search}%"
        params.extend([like_search, like_search, like_search, like_search])

    return connection.execute(
        f"""
        SELECT *
        FROM comments
        WHERE {" AND ".join(clauses)}
        ORDER BY
            CASE WHEN status = 'skipped' THEN datetime(skipped_until) END ASC,
            datetime(published_at) DESC,
            id DESC
        LIMIT 1
        OFFSET ?
        """,
        [*params, offset],
    ).fetchone()


def count_review_comments(
    connection: sqlite3.Connection,
    status_filter: str = "pending",
    search: str = "",
) -> int:
    """Count top-level comments for the current review filter."""
    clauses = ["COALESCE(is_reply, 0) = 0"]
    params = []

    if status_filter == "pending":
        clauses.append(
            """
            (
                status = 'synced'
                OR (
                    status = 'skipped'
                    AND (
                        skipped_until IS NULL
                        OR datetime(skipped_until) <= datetime('now')
                    )
                )
            )
            """
        )
    elif status_filter in {
        "skipped",
        "ignored",
        "needs_research",
        "externally_replied",
        "replied",
    }:
        clauses.append("status = ?")
        params.append(status_filter)
    else:
        clauses.append(
            """
            status NOT IN (
                'replied',
                'externally_replied',
                'ignored'
            )
            """
        )

    if search:
        clauses.append(
            """
            (
                text LIKE ?
                OR author_name LIKE ?
                OR video_title LIKE ?
                OR notes LIKE ?
            )
            """
        )
        like_search = f"%{search}%"
        params.extend([like_search, like_search, like_search, like_search])

    row = connection.execute(
        f"""
        SELECT COUNT(*) AS total
        FROM comments
        WHERE {" AND ".join(clauses)}
        """,
        params,
    ).fetchone()
    return int(row["total"])


def get_comment_by_id(
    connection: sqlite3.Connection, comment_id: int
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM comments WHERE id = ?",
        (comment_id,),
    ).fetchone()


def mark_comment_replied(
    connection: sqlite3.Connection,
    comment_id: int,
    reply_text: str,
    youtube_reply_id: str,
) -> None:
    """Mark a top-level comment as replied to through Wingman."""
    record_action(connection, comment_id, "reply", youtube_reply_id)
    connection.execute(
        """
        UPDATE comments
        SET status = 'replied',
            wingman_reply_text = ?,
            youtube_reply_id = ?,
            replied_at = ?
        WHERE id = ?
        """,
        (reply_text, youtube_reply_id, utc_now_iso(), comment_id),
    )
    connection.commit()


def reconcile_external_reply_status(
    connection: sqlite3.Connection,
    youtube_comment_id: str,
    has_channel_reply: bool,
) -> None:
    """Keep local workflow aligned with replies that already exist on YouTube."""
    if has_channel_reply:
        connection.execute(
            """
            UPDATE comments
            SET status = 'externally_replied'
            WHERE youtube_comment_id = ?
              AND COALESCE(is_reply, 0) = 0
              AND status != 'replied'
            """,
            (youtube_comment_id,),
        )
    else:
        connection.execute(
            """
            UPDATE comments
            SET status = 'synced'
            WHERE youtube_comment_id = ?
              AND COALESCE(is_reply, 0) = 0
              AND status = 'externally_replied'
            """,
            (youtube_comment_id,),
        )


def record_action(
    connection: sqlite3.Connection,
    comment_id: int,
    action: str,
    youtube_reply_id: str | None = None,
) -> None:
    comment = get_comment_by_id(connection, comment_id)
    if not comment:
        return

    connection.execute(
        """
        INSERT INTO action_history (
            comment_id,
            action,
            previous_status,
            previous_wingman_reply_text,
            previous_youtube_reply_id,
            previous_replied_at,
            previous_skipped_until,
            previous_last_seen_at,
            previous_notes,
            youtube_reply_id,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            comment_id,
            action,
            comment["status"],
            comment["wingman_reply_text"],
            comment["youtube_reply_id"],
            comment["replied_at"],
            comment["skipped_until"],
            comment["last_seen_at"],
            comment["notes"],
            youtube_reply_id,
            utc_now_iso(),
        ),
    )


def update_comment_status(
    connection: sqlite3.Connection,
    comment_id: int,
    status: str,
    action: str,
    skip_minutes: int = 60,
) -> None:
    """Update a comment's local review status."""
    record_action(connection, comment_id, action)
    skipped_until = utc_future_iso(skip_minutes) if status == "skipped" else None
    connection.execute(
        """
        UPDATE comments
        SET status = ?,
            skipped_until = ?,
            last_seen_at = ?
        WHERE id = ?
        """,
        (status, skipped_until, utc_now_iso(), comment_id),
    )
    connection.commit()


def update_comment_notes(
    connection: sqlite3.Connection, comment_id: int, notes: str
) -> None:
    record_action(connection, comment_id, "notes")
    connection.execute(
        """
        UPDATE comments
        SET notes = ?,
            last_seen_at = ?
        WHERE id = ?
        """,
        (notes, utc_now_iso(), comment_id),
    )
    connection.commit()


def save_ai_draft(
    connection: sqlite3.Connection,
    comment_id: int,
    draft_text: str,
    model: str,
    provider: str,
    prompt_version: str,
    prompt_text: str,
    status: str = "generated",
) -> None:
    """Log an AI draft and store it as the latest draft on the comment."""
    created_at = utc_now_iso()
    connection.execute(
        """
        INSERT INTO ai_drafts (
            comment_id,
            model,
            provider,
            prompt_version,
            prompt_text,
            suggested_reply,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            comment_id,
            model,
            provider,
            prompt_version,
            prompt_text,
            draft_text,
            status,
            created_at,
        ),
    )
    connection.execute(
        """
        UPDATE comments
        SET ai_draft_text = ?,
            ai_draft_model = ?,
            ai_draft_provider = ?,
            ai_drafted_at = ?
        WHERE id = ?
        """,
        (draft_text, model, provider, created_at, comment_id),
    )
    connection.commit()


def get_last_action(connection: sqlite3.Connection) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT *
        FROM action_history
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()


def delete_action(connection: sqlite3.Connection, action_id: int) -> None:
    connection.execute("DELETE FROM action_history WHERE id = ?", (action_id,))


def restore_action(connection: sqlite3.Connection, action: sqlite3.Row) -> None:
    """Restore local comment fields to their previous values."""
    connection.execute(
        """
        UPDATE comments
        SET status = ?,
            wingman_reply_text = ?,
            youtube_reply_id = ?,
            replied_at = ?,
            skipped_until = ?,
            last_seen_at = ?,
            notes = ?
        WHERE id = ?
        """,
        (
            action["previous_status"],
            action["previous_wingman_reply_text"],
            action["previous_youtube_reply_id"],
            action["previous_replied_at"],
            action["previous_skipped_until"],
            action["previous_last_seen_at"],
            action["previous_notes"],
            action["comment_id"],
        ),
    )
    delete_action(connection, action["id"])
    connection.commit()
