"""Route and action tests for the minimal Wingman Flask UI."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import create_app
from classification_service import CommentClassification
from comment_store import connect_database, sync_comments
from fetch_comments import Comment as FetchedComment
from inbox_repository import CommentRepository
from video_catalog import Video, store_discovered_videos
from youtube_reply import PostedReply


class FakeProductionClassifier:
    model = "test-classification-model"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def classify(
        self, comment_text: str, prompt: str | None = None
    ) -> CommentClassification:
        self.calls.append(comment_text)
        return CommentClassification(
            category="technical_question",
            priority=0.88,
            reply_worthy=True,
            needs_research=False,
            reason="A useful production comment.",
        )

    def classify_comment(self, comment: object) -> CommentClassification:
        return self.classify(comment.text)


class FakeReplyGenerator:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def generate_for_comment(self, comment: object) -> str:
        self.calls.append(comment)
        return "Generated reply draft."


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
                    fetched_comment("comment-low", "Low Priority Author"),
                    fetched_comment("comment-unclassified", "Unclassified Author"),
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
            repository.save_classification(
                "comment-new",
                category="community_connection",
                priority=0.95,
                reply_worthy=True,
                needs_research=False,
                reason="Meaningful personal impact.",
                classification_model="old-model",
                classification_version="production-v1",
                classified_at="2026-07-25T12:01:00Z",
            )
            repository.save_classification(
                "comment-low",
                category="generic_praise",
                priority=0.25,
                reply_worthy=True,
                needs_research=False,
                reason="Genuine but lower priority.",
                classification_model="old-model",
                classification_version="production-v1",
                classified_at="2026-07-25T12:00:00Z",
            )
        finally:
            connection.close()

        self.reply_calls: list[tuple[object, str, str]] = []

        def post_reply(youtube: object, comment_id: str, text: str) -> PostedReply:
            self.reply_calls.append((youtube, comment_id, text))
            return PostedReply(
                reply_id="youtube-reply-1",
                text=text,
                published_at="2026-07-25T15:00:00Z",
            )

        self.youtube = object()
        self.classifier = FakeProductionClassifier()
        self.reply_generator = FakeReplyGenerator()
        app = create_app(
            {
                "TESTING": True,
                "DATABASE": self.database_path,
                "YOUTUBE_CLIENT": self.youtube,
                "YOUTUBE_REPLY_SERVICE": post_reply,
                "CLASSIFICATION_SERVICE": self.classifier,
                "CLASSIFICATION_DRY_RUN": False,
                "REPLY_GENERATION_SERVICE": self.reply_generator,
            }
        )
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

    def test_inbox_lists_ranked_classified_comments_and_debug_fields(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Full text for comment-new", response.data)
        self.assertIn(b"Full text for comment-low", response.data)
        self.assertIn(b"PID Explained in 60 Seconds", response.data)
        self.assertIn(b"2026-07-25T10:00:00Z", response.data)
        self.assertIn(b"Critical", response.data)
        self.assertIn(b"Priority score", response.data)
        self.assertIn(b"0.95", response.data)
        self.assertIn(b"community_connection", response.data)
        self.assertIn(b"Meaningful personal impact.", response.data)
        self.assertIn(b"old-model", response.data)
        self.assertIn(b"production-v1", response.data)
        self.assertIn(b"Generate Draft", response.data)
        self.assertIn(b"Reclassify", response.data)
        self.assertLess(
            response.data.index(b"Full text for comment-new"),
            response.data.index(b"Full text for comment-low"),
        )
        self.assertIn(b"Needs research", response.data)
        self.assertNotIn(b"Ignored Author", response.data)
        self.assertNotIn(b"Replied Author", response.data)
        self.assertEqual(self.classifier.calls, [])

    def test_unclassified_comments_have_manual_and_batch_actions(self) -> None:
        response = self.client.get("/")

        self.assertIn(b"Unclassified", response.data)
        self.assertIn(b"Full text for comment-unclassified", response.data)
        self.assertIn(b"Classify comment", response.data)
        self.assertIn(b"Classify next 10", response.data)
        self.assertIn(b"Classify all", response.data)

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

        self.client.post("/comments/comment-new/research")
        self.assertEqual(self.row("comment-new")["needs_research"], 0)

    def test_mark_replied_action(self) -> None:
        response = self.client.post("/comments/comment-new/replied")

        self.assertEqual(response.status_code, 302)
        row = self.row("comment-new")
        self.assertEqual(row["status"], "replied")
        self.assertIsNotNone(row["replied_at"])
        self.assertNotIn(
            b"Full text for comment-new", self.client.get("/").data
        )

    def test_classifies_and_persists_real_comment(self) -> None:
        response = self.client.post(
            "/comments/comment-unclassified/classify",
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.classifier.calls,
            ["Full text for comment-unclassified"],
        )
        row = self.row("comment-unclassified")
        self.assertEqual(row["category"], "technical_question")
        self.assertEqual(float(row["priority"]), 0.88)
        self.assertEqual(row["reply_worthy"], 1)
        self.assertEqual(row["needs_research"], 0)
        self.assertEqual(
            row["classification_reason"], "A useful production comment."
        )
        self.assertIsNotNone(row["classified_at"])
        self.assertEqual(
            row["classification_model"], "test-classification-model"
        )
        self.assertEqual(row["classification_version"], "production-v2")
        self.assertIn(b"Full text for comment-unclassified", response.data)
        self.assertIn(b"High", response.data)
        self.assertIn(b"0.88", response.data)

    def test_reclassifies_existing_comment(self) -> None:
        response = self.client.post(
            "/comments/comment-new/classify",
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(float(self.row("comment-new")["priority"]), 0.88)
        self.assertIn(b"Reclassify", response.data)

    def test_batch_actions_limit_top_ten_then_classify_the_rest(self) -> None:
        connection = connect_database(self.database_path)
        try:
            sync_comments(
                connection,
                [
                    fetched_comment(f"batch-{index:02d}", f"Batch Author {index}")
                    for index in range(11)
                ],
            )
        finally:
            connection.close()

        top_ten = self.client.post("/inbox/classify-top-10")
        self.assertEqual(top_ten.status_code, 302)
        self.assertEqual(len(self.classifier.calls), 10)

        classify_rest = self.client.post("/inbox/classify-all")
        self.assertEqual(classify_rest.status_code, 302)
        self.assertEqual(len(self.classifier.calls), 12)

    def test_dry_run_displays_result_without_updating_database(self) -> None:
        dry_classifier = FakeProductionClassifier()
        dry_app = create_app(
            {
                "TESTING": True,
                "DATABASE": self.database_path,
                "CLASSIFICATION_SERVICE": dry_classifier,
                "CLASSIFICATION_DRY_RUN": True,
            }
        )
        dry_client = dry_app.test_client()

        response = dry_client.post(
            "/comments/comment-unclassified/classify",
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Dry run", response.data)
        self.assertIn(b"0.88", response.data)
        row = self.row("comment-unclassified")
        self.assertIsNone(row["classified_at"])
        self.assertIsNone(row["priority"])

        restarted_app = create_app(
            {
                "TESTING": True,
                "DATABASE": self.database_path,
                "CLASSIFICATION_SERVICE": FakeProductionClassifier(),
                "CLASSIFICATION_DRY_RUN": True,
            }
        )
        restarted_page = restarted_app.test_client().get("/")
        self.assertIn(b"Classify comment", restarted_page.data)
        self.assertNotIn(b"0.88", restarted_page.data)

    def test_generates_edits_and_locally_approves_reply(self) -> None:
        detail = self.client.get("/comments/comment-new")
        self.assertIn(b"Generate Draft", detail.data)
        self.assertNotIn(b"Post reply", detail.data)

        response = self.client.post(
            "/comments/comment-new/generate-reply",
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.reply_generator.calls), 1)
        row = self.row("comment-new")
        self.assertEqual(row["draft_reply"], "Generated reply draft.")
        self.assertIsNone(row["final_reply"])
        self.assertIn(b"Generated reply draft.", response.data)

        edited = self.client.post(
            "/comments/comment-new/draft-reply",
            data={"draft_reply": "Edited by Fred."},
            follow_redirects=True,
        )
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(self.row("comment-new")["draft_reply"], "Edited by Fred.")

        approved = self.client.post(
            "/comments/comment-new/approve-reply",
            data={"draft_reply": "Final edit from the approval screen."},
            follow_redirects=True,
        )
        self.assertEqual(approved.status_code, 200)
        row = self.row("comment-new")
        self.assertEqual(row["status"], "approved")
        self.assertEqual(row["draft_reply"], "Final edit from the approval screen.")
        self.assertEqual(row["final_reply"], "Final edit from the approval screen.")
        self.assertIsNotNone(row["reply_approved_at"])
        self.assertIn(b"Approved", approved.data)
        self.assertEqual(self.reply_calls, [])

    def test_ignored_view_lists_ignored_conversations(self) -> None:
        response = self.client.get("/?view=ignored")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Ignored Author", response.data)
        self.assertNotIn(b"New Author", response.data)
        self.assertIn(b"Move to inbox", response.data)

    def test_video_context_can_be_edited(self) -> None:
        response = self.client.post(
            "/videos/abcdefghijk/summary",
            data={"summary": "A concise explanation of PID control."},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"A concise explanation of PID control.", response.data)
        connection = connect_database(self.database_path)
        try:
            summary = connection.execute(
                "SELECT summary FROM videos WHERE video_id = 'abcdefghijk'"
            ).fetchone()["summary"]
        finally:
            connection.close()
        self.assertEqual(summary, "A concise explanation of PID control.")


if __name__ == "__main__":
    unittest.main()
