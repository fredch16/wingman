"""Route tests for the isolated classification playground."""

import html
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import create_app
from classification_playground import PLAYGROUND_COMMENTS
from classification_service import CommentClassification
from comment_store import connect_database, sync_comments
from fetch_comments import Comment as FetchedComment


class FakeClassificationService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def classify(self, comment_text: str) -> CommentClassification:
        self.calls.append(comment_text)
        return CommentClassification(
            category="test_category",
            priority=0.75,
            reply_worthy=True,
            needs_research=False,
            reason="A deterministic test classification.",
        )


class ClassificationPlaygroundRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.temporary_directory.name) / "test.db")
        connection = connect_database(self.database_path)
        try:
            sync_comments(
                connection,
                [
                    FetchedComment(
                        comment_id="production-comment",
                        thread_id="production-thread",
                        video_id="abcdefghijk",
                        author_display_name="Real Author",
                        author_channel_id=None,
                        text="Stored production comment",
                        like_count=0,
                        published_at="2026-07-25T10:00:00Z",
                        updated_at="2026-07-25T10:00:00Z",
                        total_reply_count=0,
                        can_reply=True,
                        is_public=True,
                    )
                ],
            )
        finally:
            connection.close()

        self.service = FakeClassificationService()
        app = create_app(
            {
                "TESTING": True,
                "DATABASE": self.database_path,
                "OPENAI_MODEL": "test-model",
                "CLASSIFICATION_SERVICE": self.service,
            }
        )
        self.client = app.test_client()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_page_contains_all_hardcoded_scenarios(self) -> None:
        response = self.client.get("/classification-playground")
        rendered_text = html.unescape(response.data.decode())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.count(b'class="playground-card"'), 10)
        for comment in PLAYGROUND_COMMENTS:
            self.assertIn(comment.scenario, rendered_text)
            self.assertIn(comment.text, rendered_text)
            self.assertIn(comment.expected_intent, rendered_text)
        self.assertIn(b"Expected intent", response.data)
        self.assertNotIn(b"Quick acknowledgement", response.data)
        self.assertEqual(self.service.calls, [])

        self.assertEqual(
            {comment.comment_id for comment in PLAYGROUND_COMMENTS},
            {
                "generic-praise",
                "heartfelt-thanks",
                "inspired-engineer",
                "returning-viewer",
                "technical-correction",
                "technical-question",
                "content-idea",
                "spam",
                "first",
                "shared-experience",
            },
        )

    def test_classify_one_displays_result(self) -> None:
        comment = PLAYGROUND_COMMENTS[0]

        response = self.client.post(
            f"/classification-playground/classify/{comment.comment_id}",
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.service.calls, [comment.text])
        self.assertIn(b"test_category", response.data)
        self.assertIn(b"0.75", response.data)
        self.assertIn(b"A deterministic test classification.", response.data)

    def test_classify_all_classifies_ten_without_writing_database(self) -> None:
        response = self.client.post(
            "/classification-playground/classify-all", follow_redirects=True
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.service.calls), 10)
        self.assertEqual(response.data.count(b"test_category"), 10)

        connection = sqlite3.connect(self.database_path)
        try:
            row = connection.execute(
                """
                SELECT category, priority, classification_reason
                FROM comments
                WHERE comment_id = 'production-comment'
                """
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row, (None, None, None))

    def test_unknown_comment_returns_not_found(self) -> None:
        response = self.client.post(
            "/classification-playground/classify/not-a-comment"
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
