"""Safety and deduplication tests for opt-in YouTube keyword replies."""

import sqlite3
import unittest
from dataclasses import replace
from unittest.mock import patch

from wingman.db.automation_repository import AutomationRepository
from wingman.db.comment_store import sync_comments
from wingman.db.inbox_repository import CommentRepository
from wingman.db.video_catalog import Video, store_discovered_videos
from wingman.youtube.automation import run_youtube_auto_replies
from wingman.youtube.reply import PostedReply
from wingman.youtube.sync import Comment


class YouTubeAutomationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        store_discovered_videos(self.connection, [
            Video("youtube-one", "A Short", "2026-09-20T00:00:00Z", "")
        ])
        self.repo = AutomationRepository(self.connection)
        self.rule_id = self.repo.create(
            "youtube-one", "PCB", "Here is the link: https://example.com/pcb",
            mode="youtube_reply",
        )
        self.comment = Comment(
            comment_id="new-comment", thread_id="thread-one", video_id="youtube-one",
            author_display_name="Viewer", author_channel_id="viewer-id", text="pcb please",
            like_count=0, published_at="2099-01-01T00:00:00Z",
            updated_at="2099-01-01T00:00:00Z", total_reply_count=0,
            can_reply=True, is_public=True,
        )

    def tearDown(self) -> None:
        self.connection.close()

    def store(self, comment: Comment) -> None:
        sync_comments(self.connection, [comment])

    def test_only_future_matching_comment_posts_once(self) -> None:
        self.store(self.comment)
        posted = PostedReply("reply-one", "Here is the link: https://example.com/pcb", "2099-01-01T01:00:00Z")
        with patch("wingman.youtube.automation.post_comment_reply", return_value=posted) as send:
            first = run_youtube_auto_replies(object(), self.connection, [self.comment])
            second = run_youtube_auto_replies(object(), self.connection, [self.comment])
        self.assertEqual((first.sent, second.sent, second.skipped), (1, 0, 1))
        send.assert_called_once()
        self.assertEqual(CommentRepository(self.connection).get_comment("new-comment").status, "replied")
        self.assertEqual(self.repo.delivery("new-comment")["status"], "completed")

    def test_old_comments_and_existing_replies_are_not_posted(self) -> None:
        old = replace(self.comment, comment_id="old", published_at="2020-01-01T00:00:00Z")
        replied = replace(self.comment, comment_id="replied", total_reply_count=1)
        self.store(old)
        self.store(replied)
        with patch("wingman.youtube.automation.post_comment_reply") as send:
            summary = run_youtube_auto_replies(object(), self.connection, [old, replied])
        self.assertEqual((summary.sent, summary.skipped), (0, 2))
        send.assert_not_called()

    def test_creator_and_manual_queue_are_not_posted(self) -> None:
        own = replace(self.comment, comment_id="own", author_display_name="@CharbonnierLabs")
        queued = replace(self.comment, comment_id="queued")
        self.store(own)
        self.store(queued)
        self.connection.execute(
            """CREATE TABLE reply_queue
               (comment_id TEXT PRIMARY KEY, status TEXT NOT NULL)"""
        )
        self.connection.execute(
            "INSERT INTO reply_queue VALUES ('queued', 'sending')"
        )
        with patch("wingman.youtube.automation.post_comment_reply") as send:
            summary = run_youtube_auto_replies(object(), self.connection, [own, queued])
        self.assertEqual(summary.skipped, 2)
        send.assert_not_called()

    def test_failure_stays_claimed_without_automatic_retry(self) -> None:
        self.store(self.comment)
        with patch("wingman.youtube.automation.post_comment_reply", side_effect=RuntimeError("timeout")) as send:
            first = run_youtube_auto_replies(object(), self.connection, [self.comment])
            second = run_youtube_auto_replies(object(), self.connection, [self.comment])
        self.assertEqual((first.failed, second.skipped), (1, 1))
        self.assertEqual(send.call_count, 1)
        self.assertEqual(self.repo.delivery("new-comment")["status"], "failed")

    def test_rule_requires_youtube_video(self) -> None:
        store_discovered_videos(self.connection, [
            Video("instagram-one", "Reel", "2026-09-20T00:00:00Z", "", platform="instagram")
        ])
        with self.assertRaisesRegex(ValueError, "YouTube video"):
            self.repo.create("instagram-one", "PCB", "Reply", mode="youtube_reply")


if __name__ == "__main__":
    unittest.main()
