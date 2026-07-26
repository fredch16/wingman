"""Instagram synchronization and publishing integration tests."""

import tempfile
import unittest
from pathlib import Path

from wingman.db.comment_store import connect_database
from wingman.db.inbox_repository import CommentRepository
from wingman.instagram.client import PostedReply
from wingman.instagram.sync import group_comment_threads, sync_instagram
from wingman.web import create_app


class FakeInstagramClient:
    def authenticated_account(self) -> dict:
        return {
            "id": "profile-id",
            "user_id": "account-id",
            "username": "charbonnierlabs",
        }

    def media(self, account_id: str) -> list[dict]:
        assert account_id == "account-id"
        return [
            {
                "id": "media-1",
                "caption": "Button debounce explained\nMore detail.",
                "timestamp": "2026-07-25T09:00:00Z",
                "thumbnail_url": "https://example.test/reel.jpg",
                "permalink": "https://www.instagram.com/reel/example/",
            }
        ]

    def comments(self, media_id: str) -> list[dict]:
        assert media_id == "media-1"
        return [
            {
                "id": "viewer-answered",
                "text": "How does this work?",
                "from": {"id": "viewer-1", "username": "curious_engineer"},
                "timestamp": "2026-07-25T10:00:00Z",
                "like_count": 2,
            },
            {
                "id": "creator-reply",
                "parent_id": "viewer-answered",
                "text": "It filters repeated transitions.",
                "from": {"id": "account-id", "username": "charbonnierlabs"},
                "timestamp": "2026-07-25T10:05:00Z",
                "like_count": 1,
            },
            {
                "id": "viewer-open",
                "text": "Could this run without delay?",
                "from": {"id": "viewer-2", "username": "embedded_friend"},
                "timestamp": "2026-07-25T11:00:00Z",
                "like_count": 4,
            },
            {
                "id": "viewer-follow-up",
                "parent_id": "viewer-open",
                "text": "I am wondering too.",
                "from": {"id": "viewer-3", "username": "another_viewer"},
                "timestamp": "2026-07-25T11:01:00Z",
                "like_count": 0,
            },
            {
                "id": "creator-root",
                "text": "Creator-authored note.",
                "from": {"id": "account-id", "username": "charbonnierlabs"},
                "timestamp": "2026-07-25T12:00:00Z",
                "like_count": 0,
            },
        ]


class InstagramIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = str(
            Path(self.temporary_directory.name) / "instagram.db"
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_groups_flat_instagram_replies_under_top_level_comment(self) -> None:
        rows = FakeInstagramClient().comments("media-1")

        roots, replies = group_comment_threads(rows)

        self.assertEqual(
            [row["id"] for row in roots],
            ["viewer-answered", "viewer-open", "creator-root"],
        )
        self.assertEqual(
            [row["id"] for row in replies["viewer-open"]],
            ["viewer-follow-up"],
        )

    def test_syncs_media_and_keeps_only_unanswered_viewer_threads_active(self) -> None:
        media_count, comment_count = sync_instagram(
            FakeInstagramClient(), self.database_path
        )

        self.assertEqual((media_count, comment_count), (1, 3))
        connection = connect_database(self.database_path)
        try:
            repository = CommentRepository(connection)
            active = repository.list_active_inbox_comments()
            self.assertEqual(
                [comment.comment_id for comment in active], ["viewer-open"]
            )
            self.assertEqual(active[0].platform, "instagram")
            self.assertEqual(
                active[0].permalink,
                "https://www.instagram.com/reel/example/",
            )
            answered = repository.get_comment("viewer-answered")
            assert answered is not None
            self.assertTrue(answered.has_creator_reply)
            self.assertEqual(answered.creator_reply_id, "creator-reply")
        finally:
            connection.close()

    def test_posts_selected_instagram_reply_through_platform_service(self) -> None:
        sync_instagram(FakeInstagramClient(), self.database_path)
        calls: list[tuple[object, str, str]] = []
        configured_client = object()

        def post_reply(client: object, comment_id: str, text: str) -> PostedReply:
            calls.append((client, comment_id, text))
            return PostedReply(
                reply_id="published-instagram-reply",
                text=text,
                published_at="2026-07-25T13:00:00Z",
            )

        app = create_app(
            {
                "TESTING": True,
                "DATABASE": self.database_path,
                "INSTAGRAM_CLIENT": configured_client,
                "INSTAGRAM_REPLY_SERVICE": post_reply,
            }
        )
        response = app.test_client().post(
            "/comments/viewer-open/reply",
            data={"draft_reply": "Yes — use a non-blocking timer."},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            calls,
            [
                (
                    configured_client,
                    "viewer-open",
                    "Yes — use a non-blocking timer.",
                )
            ],
        )
        connection = connect_database(self.database_path)
        try:
            row = connection.execute(
                "SELECT status, creator_reply_id FROM comments "
                "WHERE comment_id = 'viewer-open'"
            ).fetchone()
            self.assertEqual(row["status"], "replied")
            self.assertEqual(
                row["creator_reply_id"], "published-instagram-reply"
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
