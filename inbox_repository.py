"""Reusable repository for Wingman inbox comments."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from comment_store import create_comments_table
from video_catalog import create_videos_table


@dataclass(frozen=True)
class Comment:
    """A comment as presented and managed in the Wingman inbox."""

    comment_id: str
    video_id: str
    video_title: str
    author_display_name: str
    text: str
    published_at: str
    status: str
    priority: str | None
    category: str | None
    classification_reason: str | None
    draft_reply: str | None
    final_reply: str | None
    needs_research: bool
    is_ignored: bool
    replied_at: str | None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class CommentRepository:
    """Read and update inbox state independently of any UI."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        create_comments_table(connection)
        create_videos_table(connection)

    def list_active_inbox_comments(self) -> list[Comment]:
        return self.list_inbox_comments("all")

    def list_inbox_comments(self, filter_name: str = "all") -> list[Comment]:
        filters = {
            "all": "comments.is_ignored = 0 AND comments.status != 'replied'",
            "new": (
                "comments.is_ignored = 0 AND comments.status = 'new'"
            ),
            "needs_research": (
                "comments.is_ignored = 0 AND comments.status != 'replied' "
                "AND comments.needs_research = 1"
            ),
            "ignored": "comments.is_ignored = 1",
            "replied": "comments.status = 'replied'",
        }
        if filter_name not in filters:
            raise ValueError(f"Unknown inbox filter: {filter_name}")
        rows = self.connection.execute(
            f"""
            SELECT
                comments.comment_id,
                comments.video_id,
                COALESCE(videos.title, comments.video_id) AS video_title,
                comments.author_display_name,
                comments.text,
                comments.published_at,
                comments.status,
                comments.priority,
                comments.category,
                comments.classification_reason,
                comments.draft_reply,
                comments.final_reply,
                comments.needs_research,
                comments.is_ignored,
                comments.replied_at
            FROM comments
            LEFT JOIN videos ON videos.video_id = comments.video_id
            WHERE {filters[filter_name]}
            ORDER BY comments.published_at DESC, comments.comment_id
            """
        ).fetchall()
        return [self._comment_from_row(row) for row in rows]

    def get_comment(self, comment_id: str) -> Comment | None:
        row = self.connection.execute(
            """
            SELECT
                comments.comment_id,
                comments.video_id,
                COALESCE(videos.title, comments.video_id) AS video_title,
                comments.author_display_name,
                comments.text,
                comments.published_at,
                comments.status,
                comments.priority,
                comments.category,
                comments.classification_reason,
                comments.draft_reply,
                comments.final_reply,
                comments.needs_research,
                comments.is_ignored,
                comments.replied_at
            FROM comments
            LEFT JOIN videos ON videos.video_id = comments.video_id
            WHERE comments.comment_id = ?
            """,
            (comment_id,),
        ).fetchone()
        return self._comment_from_row(row) if row else None

    def mark_ignored(self, comment_id: str) -> bool:
        return self._update(
            comment_id,
            "is_ignored = 1, status = 'ignored'",
            (),
        )

    def unignore(self, comment_id: str) -> bool:
        return self._update(
            comment_id,
            "is_ignored = 0, status = 'new'",
            (),
        )

    def mark_needs_research(
        self, comment_id: str, needs_research: bool = True
    ) -> bool:
        return self._update(
            comment_id,
            "needs_research = ?",
            (int(needs_research),),
        )

    def mark_replied(
        self,
        comment_id: str,
        final_reply: str | None = None,
        replied_at: str | None = None,
    ) -> bool:
        return self._update(
            comment_id,
            "status = 'replied', final_reply = ?, replied_at = ?",
            (final_reply, replied_at or utc_now()),
        )

    def update_priority(self, comment_id: str, priority: str | None) -> bool:
        return self._update(comment_id, "priority = ?", (priority,))

    def update_category(self, comment_id: str, category: str | None) -> bool:
        return self._update(comment_id, "category = ?", (category,))

    def update_draft_reply(
        self, comment_id: str, draft_reply: str | None
    ) -> bool:
        return self._update(comment_id, "draft_reply = ?", (draft_reply,))

    def _update(
        self, comment_id: str, assignments: str, values: tuple[object, ...]
    ) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                f"UPDATE comments SET {assignments} WHERE comment_id = ?",
                (*values, comment_id),
            )
        return cursor.rowcount > 0

    @staticmethod
    def _comment_from_row(row: sqlite3.Row) -> Comment:
        return Comment(
            comment_id=row["comment_id"],
            video_id=row["video_id"],
            video_title=row["video_title"],
            author_display_name=row["author_display_name"],
            text=row["text"],
            published_at=row["published_at"],
            status=row["status"],
            priority=row["priority"],
            category=row["category"],
            classification_reason=row["classification_reason"],
            draft_reply=row["draft_reply"],
            final_reply=row["final_reply"],
            needs_research=bool(row["needs_research"]),
            is_ignored=bool(row["is_ignored"]),
            replied_at=row["replied_at"],
        )
