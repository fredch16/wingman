"""Posting stays responsive while the platform request runs in the queue."""

import tempfile
import time
import unittest
from pathlib import Path
from threading import Event

from wingman.db.comment_store import connect_database, sync_comments
from wingman.db.inbox_repository import CommentRepository
from wingman.reply_queue import ReplyQueue
from wingman.web import create_app
from wingman.youtube.reply import PostedReply
from wingman.youtube.sync import Comment


class ReplyQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.database = str(Path(self.directory.name) / "comments.db")
        connection = connect_database(self.database)
        sync_comments(connection, [Comment(
            comment_id="comment-1", thread_id="thread-1", video_id="video-1",
            author_display_name="Viewer", author_channel_id=None, text="Hello",
            like_count=0, published_at="2026-07-25T10:00:00Z",
            updated_at="2026-07-25T10:00:00Z", total_reply_count=0,
            can_reply=True, is_public=True,
        )])
        connection.close()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_request_returns_before_send_and_duplicate_is_blocked(self) -> None:
        started, release = Event(), Event()

        def send(_client, _comment_id, text):
            started.set()
            self.assertTrue(release.wait(3))
            return PostedReply("reply-1", text, "2026-07-25T11:00:00Z")

        app = create_app({
            "TESTING": True, "ASYNC_POSTS": True, "DATABASE": self.database,
            "YOUTUBE_CLIENT": object(), "YOUTUBE_REPLY_SERVICE": send,
        })
        client = app.test_client()
        try:
            response = client.post(
                "/comments/comment-1/approve-and-post",
                data={"draft_reply": "My answer"},
                headers={"Accept": "application/json"},
            )
            self.assertEqual(response.status_code, 202)
            self.assertTrue(started.wait(2))
            self.assertEqual(client.get("/comments/comment-1/reply-status").json["status"], "sending")
            duplicate = client.post(
                "/comments/comment-1/reply", data={"reply_text": "Again"},
                headers={"Accept": "application/json"},
            )
            self.assertEqual(duplicate.status_code, 409)
        finally:
            release.set()
        self._wait_for(app.extensions["reply_queue"], "sent")
        connection = connect_database(self.database)
        self.assertEqual(CommentRepository(connection).get_comment("comment-1").status, "replied")
        connection.close()

    def test_failure_is_visible_and_not_retried_automatically(self) -> None:
        calls = []

        def send(_platform, _comment_id, _text):
            calls.append(1)
            raise RuntimeError("Platform unavailable")

        queue = ReplyQueue(self.database, send)
        self.assertTrue(queue.enqueue("comment-1", "youtube", "My answer"))
        self._wait_for(queue, "failed")
        self.assertEqual(queue.status("comment-1")["error"], "Platform unavailable")
        self.assertEqual(len(calls), 1)
        self.assertEqual(queue.status("comment-1")["status"], "failed")
        app = create_app({"TESTING": True, "DATABASE": self.database})
        self.assertIn(b"Platform unavailable", app.test_client().get("/").data)

    def test_interrupted_send_is_flagged_without_resending(self) -> None:
        queue = ReplyQueue(self.database, lambda *_args: self.fail("Must not resend"))
        connection = connect_database(self.database)
        from wingman.reply_queue import create_reply_queue
        create_reply_queue(connection)
        connection.execute(
            """INSERT INTO reply_queue
               (comment_id, platform, reply_text, status, queued_at)
               VALUES ('comment-1', 'youtube', 'My answer', 'sending', '2026-07-25T10:00:00Z')"""
        )
        connection.commit()
        connection.close()
        queue.mark_interrupted()
        queue.start()
        self.assertEqual(queue.status("comment-1")["status"], "failed")
        self.assertIn("Check the platform", queue.status("comment-1")["error"])

    def _wait_for(self, queue: ReplyQueue, expected: str) -> None:
        for _ in range(100):
            if queue.status("comment-1")["status"] == expected:
                return
            time.sleep(0.02)
        self.fail(f"Reply did not reach {expected}")
