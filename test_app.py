"""Route and action tests for the minimal Wingman Flask UI."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import create_app
from comment_store import connect_database, sync_comments
from fetch_comments import Comment as FetchedComment
from inbox_repository import CommentRepository
from video_catalog import Video, store_discovered_videos


def fetched_comment(comment_id: str, author: str) -> FetchedComment:
    return FetchedComment(
        comment_id=comment_id,
        thread_id=f"thread-{comment_id}",
        video_id="abcdefghijk",
        author_display_name=author,
        author_channel_id=None,
        text=f"Full text for {comment_id}",
        like_count=1,
        published_at="2026-07-25T10:00:00Z",
        updated_at="2026-07-25T10:00:00Z",
        total_reply_count=0,
        can_reply=True,
        is_public=True,
    )


class InboxRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = str(
            Path(self.temporary_directory.name) / "test-comments.db"
        )
        connection = connect_database(self.database_path)
        try:
            store_discovered_videos(
                connection,
                [
                    Video(
                        video_id="abcdefghijk",
                        title="PID Explained in 60 Seconds",
                        published_at="2026-07-25T09:00:00Z",
                        thumbnail_url="https://img/video.jpg",
                    )
                ],
            )
            sync_comments(
                connection,
                [
                    fetched_comment("comment-new", "New Author"),
                    fetched_comment("comment-ignored", "Ignored Author"),
                    fetched_comment("comment-replied", "Replied Author"),
                ],
            )
            repository = CommentRepository(connection)
            repository.mark_ignored("comment-ignored")
            repository.mark_replied(
                "comment-replied",
                "Already answered",
                "2026-07-25T12:00:00Z",
            )
        finally:
            connection.close()

        app = create_app({"TESTING": True, "DATABASE": self.database_path})
        self.client = app.test_client()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def row(self, comment_id: str) -> sqlite3.Row:
        connection = connect_database(self.database_path)
        try:
            row = connection.execute(
                "SELECT * FROM comments WHERE comment_id = ?", (comment_id,)
            ).fetchone()
            assert row is not None
            return row
        finally:
            connection.close()

    def test_inbox_lists_active_comments_and_fields(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"New Author", response.data)
        self.assertIn(b"Full text for comment-new", response.data)
        self.assertIn(b"PID Explained in 60 Seconds", response.data)
        self.assertIn(b"2026-07-25T10:00:00Z", response.data)
        self.assertIn(b"Priority", response.data)
        self.assertIn(b"Category", response.data)
        self.assertIn(b"Needs research", response.data)
        self.assertNotIn(b"Ignored Author", response.data)
        self.assertNotIn(b"Replied Author", response.data)

    def test_filters_show_matching_comments(self) -> None:
        ignored = self.client.get("/?filter=ignored")
        replied = self.client.get("/?filter=replied")
        new = self.client.get("/?filter=new")

        self.assertIn(b"Ignored Author", ignored.data)
        self.assertNotIn(b"New Author", ignored.data)
        self.assertIn(b"Replied Author", replied.data)
        self.assertIn(b"New Author", new.data)

    def test_comment_detail_and_missing_comment(self) -> None:
        response = self.client.get("/comments/comment-new")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Full text for comment-new", response.data)
        self.assertIn(b"PID Explained in 60 Seconds", response.data)
        self.assertIn(
            b"https://www.youtube.com/watch?v=abcdefghijk&lc=comment-new",
            response.data,
        )
        self.assertEqual(self.client.get("/comments/missing").status_code, 404)

    def test_ignore_and_unignore_action(self) -> None:
        ignored = self.client.post("/comments/comment-new/ignore")
        self.assertEqual(ignored.status_code, 302)
        self.assertEqual(self.row("comment-new")["is_ignored"], 1)

        unignored = self.client.post("/comments/comment-new/ignore")
        self.assertEqual(unignored.status_code, 302)
        row = self.row("comment-new")
        self.assertEqual(row["is_ignored"], 0)
        self.assertEqual(row["status"], "new")

    def test_research_action_toggles_state_and_filter(self) -> None:
        self.client.post("/comments/comment-new/research")
        self.assertEqual(self.row("comment-new")["needs_research"], 1)
        filtered = self.client.get("/?filter=needs_research")
        self.assertIn(b"New Author", filtered.data)

        self.client.post("/comments/comment-new/research")
        self.assertEqual(self.row("comment-new")["needs_research"], 0)

    def test_mark_replied_action(self) -> None:
        response = self.client.post("/comments/comment-new/replied")

        self.assertEqual(response.status_code, 302)
        row = self.row("comment-new")
        self.assertEqual(row["status"], "replied")
        self.assertIsNotNone(row["replied_at"])
        self.assertIn(
            b"New Author", self.client.get("/?filter=replied").data
        )


if __name__ == "__main__":
    unittest.main()
