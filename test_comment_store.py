"""Tests for SQLite comment persistence."""

import sqlite3
import unittest
from dataclasses import replace

from comment_store import create_comments_table, sync_comments
from fetch_comments import Comment


def comment(comment_id: str = "comment-1") -> Comment:
    return Comment(
        comment_id=comment_id,
        thread_id=f"thread-{comment_id}",
        video_id="abcdefghijk",
        author_display_name="Author",
        author_channel_id="channel-id",
        text="Original text",
        like_count=2,
        published_at="2026-07-25T10:00:00Z",
        updated_at="2026-07-25T11:00:00Z",
        total_reply_count=3,
        can_reply=True,
        is_public=True,
    )


class CommentStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        create_comments_table(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def test_insert_stores_all_fields_and_seen_timestamps(self) -> None:
        summary = sync_comments(
            self.connection, [comment()], "2026-07-25T12:00:00Z"
        )

        row = self.connection.execute(
            "SELECT * FROM comments WHERE comment_id = ?", ("comment-1",)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(summary.newly_inserted, 1)
        self.assertEqual(row["thread_id"], "thread-comment-1")
        self.assertEqual(row["video_id"], "abcdefghijk")
        self.assertEqual(row["author_display_name"], "Author")
        self.assertEqual(row["author_channel_id"], "channel-id")
        self.assertEqual(row["text"], "Original text")
        self.assertEqual(row["like_count"], 2)
        self.assertEqual(row["published_at"], "2026-07-25T10:00:00Z")
        self.assertEqual(row["updated_at"], "2026-07-25T11:00:00Z")
        self.assertEqual(row["total_reply_count"], 3)
        self.assertEqual(row["can_reply"], 1)
        self.assertEqual(row["is_public"], 1)
        self.assertEqual(row["first_seen_at"], "2026-07-25T12:00:00Z")
        self.assertEqual(row["last_seen_at"], "2026-07-25T12:00:00Z")

    def test_update_changes_fields_and_preserves_first_seen(self) -> None:
        original = comment()
        sync_comments(self.connection, [original], "2026-07-25T12:00:00Z")

        summary = sync_comments(
            self.connection,
            [replace(original, text="Edited text", like_count=5)],
            "2026-07-25T13:00:00Z",
        )

        row = self.connection.execute(
            "SELECT * FROM comments WHERE comment_id = ?", ("comment-1",)
        ).fetchone()
        self.assertEqual(summary.updated, 1)
        self.assertEqual(row["text"], "Edited text")
        self.assertEqual(row["like_count"], 5)
        self.assertEqual(row["first_seen_at"], "2026-07-25T12:00:00Z")
        self.assertEqual(row["last_seen_at"], "2026-07-25T13:00:00Z")

    def test_migrates_existing_comments_table_with_inbox_defaults(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            CREATE TABLE comments (
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
                last_seen_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO comments VALUES (
                'legacy', 'thread', 'abcdefghijk', 'Author', NULL, 'Text',
                0, 'published', 'updated', 0, 1, 1, 'first', 'last'
            )
            """
        )

        create_comments_table(connection)

        row = connection.execute(
            """
            SELECT status, priority, category, classification_reason,
                   reply_worthy, classified_at, classification_model,
                   classification_version,
                   draft_reply, final_reply, needs_research, is_ignored,
                   replied_at, has_creator_reply, creator_reply_id,
                   creator_replied_at, reply_status_checked_at
            FROM comments WHERE comment_id = 'legacy'
            """
        ).fetchone()
        self.assertEqual(row["status"], "new")
        self.assertIsNone(row["priority"])
        self.assertIsNone(row["category"])
        self.assertIsNone(row["classification_reason"])
        self.assertIsNone(row["reply_worthy"])
        self.assertIsNone(row["classified_at"])
        self.assertIsNone(row["classification_model"])
        self.assertIsNone(row["classification_version"])
        self.assertIsNone(row["draft_reply"])
        self.assertIsNone(row["final_reply"])
        self.assertEqual(row["needs_research"], 0)
        self.assertEqual(row["is_ignored"], 0)
        self.assertIsNone(row["replied_at"])
        self.assertEqual(row["has_creator_reply"], 0)
        self.assertIsNone(row["creator_reply_id"])
        self.assertIsNone(row["creator_replied_at"])
        self.assertIsNone(row["reply_status_checked_at"])
        connection.close()

    def test_duplicate_ids_are_upserted_once(self) -> None:
        summary = sync_comments(
            self.connection,
            [comment(), replace(comment(), text="Latest duplicate")],
            "2026-07-25T12:00:00Z",
        )

        count = self.connection.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
        text = self.connection.execute("SELECT text FROM comments").fetchone()[0]
        self.assertEqual(summary.fetched, 1)
        self.assertEqual(summary.newly_inserted, 1)
        self.assertEqual(count, 1)
        self.assertEqual(text, "Latest duplicate")

    def test_repeat_sync_is_unchanged_and_refreshes_last_seen(self) -> None:
        stored_comment = comment()
        sync_comments(self.connection, [stored_comment], "2026-07-25T12:00:00Z")

        summary = sync_comments(
            self.connection, [stored_comment], "2026-07-25T13:00:00Z"
        )

        row = self.connection.execute(
            "SELECT first_seen_at, last_seen_at FROM comments"
        ).fetchone()
        self.assertEqual(summary.newly_inserted, 0)
        self.assertEqual(summary.updated, 0)
        self.assertEqual(summary.unchanged, 1)
        self.assertEqual(row["first_seen_at"], "2026-07-25T12:00:00Z")
        self.assertEqual(row["last_seen_at"], "2026-07-25T13:00:00Z")


if __name__ == "__main__":
    unittest.main()
