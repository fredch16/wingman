"""Durable, single-worker queue for replies initiated from the inbox."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from threading import Lock, Thread
from typing import Callable

from wingman.db.comment_store import connect_database
from wingman.db.inbox_repository import CommentRepository, utc_now

SendReply = Callable[[str, str, str], object]


def create_reply_queue(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS reply_queue (
            comment_id TEXT PRIMARY KEY,
            platform TEXT NOT NULL,
            reply_text TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT,
            queued_at TEXT NOT NULL,
            finished_at TEXT
        )"""
    )
    connection.commit()


class ReplyQueue:
    def __init__(self, database_path: str, send: SendReply) -> None:
        self.database_path = database_path
        self.send = send
        self.lock = Lock()
        self.worker: Thread | None = None

    def mark_interrupted(self) -> None:
        """Never resend an uncertain in-flight post after a server restart."""
        with closing(connect_database(self.database_path)) as connection, connection:
            create_reply_queue(connection)
            connection.execute(
                """UPDATE reply_queue SET status = 'failed',
                   error = 'Send interrupted. Check the platform before retrying.',
                   finished_at = ? WHERE status = 'sending'""",
                (utc_now(),),
            )

    def enqueue(self, comment_id: str, platform: str, reply_text: str) -> bool:
        with closing(connect_database(self.database_path)) as connection, connection:
            create_reply_queue(connection)
            existing = connection.execute(
                "SELECT status FROM reply_queue WHERE comment_id = ?", (comment_id,)
            ).fetchone()
            if existing and existing["status"] in {"queued", "sending", "sent"}:
                return False
            connection.execute(
                """INSERT INTO reply_queue
                   (comment_id, platform, reply_text, status, error, queued_at, finished_at)
                   VALUES (?, ?, ?, 'queued', NULL, ?, NULL)
                   ON CONFLICT(comment_id) DO UPDATE SET
                   platform=excluded.platform, reply_text=excluded.reply_text,
                   status='queued', error=NULL, queued_at=excluded.queued_at,
                   finished_at=NULL""",
                (comment_id, platform, reply_text, utc_now()),
            )
        self.start()
        return True

    def status(self, comment_id: str) -> dict[str, str | None] | None:
        with closing(connect_database(self.database_path)) as connection, connection:
            create_reply_queue(connection)
            row = connection.execute(
                "SELECT status, error FROM reply_queue WHERE comment_id = ?", (comment_id,)
            ).fetchone()
            return dict(row) if row else None

    def failed(self) -> list[dict[str, str]]:
        with closing(connect_database(self.database_path)) as connection, connection:
            create_reply_queue(connection)
            return [dict(row) for row in connection.execute(
                """SELECT comment_id, error FROM reply_queue WHERE status = 'failed'
                   ORDER BY finished_at DESC"""
            )]

    def start(self) -> None:
        with self.lock:
            if self.worker and self.worker.is_alive():
                return
            self.worker = Thread(target=self._drain, daemon=True, name="wingman-replies")
            self.worker.start()

    def _drain(self) -> None:
        while True:
            with closing(connect_database(self.database_path)) as connection, connection:
                create_reply_queue(connection)
                row = connection.execute(
                    """SELECT comment_id, platform, reply_text FROM reply_queue
                       WHERE status = 'queued' ORDER BY queued_at LIMIT 1"""
                ).fetchone()
                if row is None:
                    with self.lock:
                        self.worker = None
                    return
                comment_id, platform, reply_text = row
                connection.execute(
                    "UPDATE reply_queue SET status = 'sending' WHERE comment_id = ?",
                    (comment_id,),
                )
            try:
                posted = self.send(platform, comment_id, reply_text)
                with closing(connect_database(self.database_path)) as connection, connection:
                    repository = CommentRepository(connection)
                    repository.mark_platform_replied(
                        comment_id, posted.reply_id, posted.text, posted.published_at
                    )
                    connection.execute(
                        """UPDATE reply_queue SET status = 'sent', finished_at = ?
                           WHERE comment_id = ?""",
                        (utc_now(), comment_id),
                    )
            except Exception as error:
                # Never auto-retry: the remote post might have succeeded before
                # an error reached us, so retrying could publish a duplicate.
                with closing(connect_database(self.database_path)) as connection, connection:
                    connection.execute(
                        """UPDATE reply_queue SET status = 'failed', error = ?,
                           finished_at = ? WHERE comment_id = ?""",
                        (str(error), utc_now(), comment_id),
                    )
