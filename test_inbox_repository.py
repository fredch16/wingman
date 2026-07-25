"""Tests for reusable Wingman inbox repository methods."""

import sqlite3
import unittest

from comment_store import sync_comments
from fetch_comments import Comment as FetchedComment
from inbox_repository import CommentRepository
from reply_detection import store_creator_channel_id
from video_catalog import Video, store_discovered_videos


def fetched_comment(comment_id: str) -> FetchedComment:
    return FetchedComment(
        comment_id=comment_id,
        thread_id=f"thread-{comment_id}",
        video_id="abcdefghijk",
        author_display_name=f"Author {comment_id}",
        author_channel_id=None,
        text=f"Text {comment_id}",
        like_count=0,
        published_at=f"2026-07-25T10:00:0{comment_id[-1]}Z",
        updated_at="2026-07-25T10:00:00Z",
        total_reply_count=0,
        can_reply=True,
        is_public=True,
    )


class CommentRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        store_discovered_videos(
            self.connection,
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
            self.connection,
            [fetched_comment("comment-1"), fetched_comment("comment-2")],
        )
        self.repository = CommentRepository(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def test_lists_active_inbox_comments_as_models(self) -> None:
        comments = self.repository.list_active_inbox_comments()

        self.assertEqual(len(comments), 2)
        self.assertEqual(comments[0].video_title, "PID Explained in 60 Seconds")
        self.assertEqual(comments[0].status, "new")
        self.assertIsNone(comments[0].priority)
        self.assertFalse(comments[0].needs_research)

    def test_updates_inbox_fields(self) -> None:
        self.assertTrue(self.repository.mark_needs_research("comment-1"))
        self.assertTrue(self.repository.update_priority("comment-1", "high"))
        self.assertTrue(self.repository.update_category("comment-1", "technical"))
        self.assertTrue(
            self.repository.update_draft_reply("comment-1", "Draft response")
        )

        row = self.connection.execute(
            """
            SELECT needs_research, priority, category, draft_reply
            FROM comments WHERE comment_id = 'comment-1'
            """
        ).fetchone()
        self.assertEqual(row["needs_research"], 1)
        self.assertEqual(row["priority"], "high")
        self.assertEqual(row["category"], "technical")
        self.assertEqual(row["draft_reply"], "Draft response")

    def test_marks_confirmed_youtube_reply(self) -> None:
        self.assertTrue(
            self.repository.mark_youtube_replied(
                "comment-1",
                reply_id="youtube-reply-1",
                final_reply="Published response",
                replied_at="2026-07-25T15:00:00Z",
            )
        )

        row = self.connection.execute(
            """
            SELECT status, final_reply, replied_at, has_creator_reply,
                   creator_reply_id, creator_replied_at, reply_status_checked_at
            FROM comments
            WHERE comment_id = 'comment-1'
            """
        ).fetchone()
        self.assertEqual(row["status"], "replied")
        self.assertEqual(row["final_reply"], "Published response")
        self.assertEqual(row["replied_at"], "2026-07-25T15:00:00Z")
        self.assertEqual(row["has_creator_reply"], 1)
        self.assertEqual(row["creator_reply_id"], "youtube-reply-1")
        self.assertEqual(row["creator_replied_at"], "2026-07-25T15:00:00Z")
        self.assertEqual(
            row["reply_status_checked_at"], "2026-07-25T15:00:00Z"
        )

    def test_ignored_comments_leave_active_inbox(self) -> None:
        self.assertTrue(self.repository.mark_ignored("comment-1"))

        active_ids = {
            comment.comment_id
            for comment in self.repository.list_active_inbox_comments()
        }
        row = self.connection.execute(
            "SELECT status, is_ignored FROM comments WHERE comment_id = 'comment-1'"
        ).fetchone()
        self.assertNotIn("comment-1", active_ids)
        self.assertEqual(row["status"], "ignored")
        self.assertEqual(row["is_ignored"], 1)

    def test_replied_comments_leave_active_inbox(self) -> None:
        self.assertTrue(
            self.repository.mark_replied(
                "comment-2",
                final_reply="Final response",
                replied_at="2026-07-25T12:00:00Z",
            )
        )

        active_ids = {
            comment.comment_id
            for comment in self.repository.list_active_inbox_comments()
        }
        row = self.connection.execute(
            """
            SELECT status, final_reply, replied_at
            FROM comments WHERE comment_id = 'comment-2'
            """
        ).fetchone()
        self.assertNotIn("comment-2", active_ids)
        self.assertEqual(row["status"], "replied")
        self.assertEqual(row["final_reply"], "Final response")
        self.assertEqual(row["replied_at"], "2026-07-25T12:00:00Z")

    def test_detected_creator_replies_leave_active_inbox(self) -> None:
        self.connection.execute(
            """
            UPDATE comments SET
                has_creator_reply = 1,
                creator_reply_id = 'reply-1',
                creator_replied_at = '2026-07-25T12:00:00Z',
                reply_status_checked_at = '2026-07-25T12:01:00Z'
            WHERE comment_id = 'comment-1'
            """
        )

        active_ids = {
            comment.comment_id
            for comment in self.repository.list_active_inbox_comments()
        }
        detected = self.repository.get_comment("comment-1")
        self.assertNotIn("comment-1", active_ids)
        self.assertIsNotNone(detected)
        self.assertTrue(detected.has_creator_reply)

    def test_creator_authored_comments_leave_inbox(self) -> None:
        store_creator_channel_id(self.connection, "creator-channel")
        self.connection.execute(
            """
            UPDATE comments
            SET author_display_name = '@CharbonnierLabs'
            WHERE comment_id = 'comment-1'
            """
        )
        self.connection.execute(
            """
            UPDATE comments
            SET author_channel_id = 'creator-channel'
            WHERE comment_id = 'comment-2'
            """
        )

        self.assertEqual(
            self.repository.list_active_inbox_comments(),
            [],
        )


if __name__ == "__main__":
    unittest.main()
