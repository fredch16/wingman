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
from learning_service import ExtractedPreference
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


class FakePreferenceLearner:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def extract_preferences(
        self, comparison: object
    ) -> list[ExtractedPreference]:
        self.calls.append(comparison)
        return [
            ExtractedPreference(
                change_type="length",
                preference="Fred prefers shorter replies.",
            ),
            ExtractedPreference(
                change_type="acknowledgement",
                preference="Fred acknowledges ideas before explaining.",
            ),
        ]


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
        self.creator_profile_path = (
            Path(self.temporary_directory.name) / "creator.md"
        )
        self.creator_profile_path.write_text(
            "# Fred\n\n## Style\n\n- Friendly.\n",
            encoding="utf-8",
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
        self.preference_learner = FakePreferenceLearner()
        app = create_app(
            {
                "TESTING": True,
                "DATABASE": self.database_path,
                "YOUTUBE_CLIENT": self.youtube,
                "YOUTUBE_REPLY_SERVICE": post_reply,
                "CLASSIFICATION_SERVICE": self.classifier,
                "CLASSIFICATION_DRY_RUN": False,
                "REPLY_GENERATION_SERVICE": self.reply_generator,
                "PREFERENCE_LEARNING_SERVICE": self.preference_learner,
                "CREATOR_PROFILE": str(self.creator_profile_path),
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

    def test_reclassifies_all_active_comments(self) -> None:
        response = self.client.post("/inbox/reclassify-all")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self.classifier.calls), 3)

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
        self.assertEqual(
            row["original_draft_reply"], "Generated reply draft."
        )
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
        self.assertIn(b"Post to YouTube", approved.data)
        self.assertEqual(self.reply_calls, [])

    def test_posts_approved_editor_text_to_youtube(self) -> None:
        self.client.post("/comments/comment-new/generate-reply")
        approved = self.client.post(
            "/comments/comment-new/approve-reply",
            data={"draft_reply": "Approved reply for YouTube."},
            follow_redirects=True,
        )
        self.assertIn(b"Post to YouTube", approved.data)
        self.assertIn(
            b'formaction="/comments/comment-new/reply"',
            approved.data,
        )

        posted = self.client.post(
            "/comments/comment-new/reply",
            data={"draft_reply": "Final text posted from the editor."},
            follow_redirects=True,
        )

        self.assertEqual(posted.status_code, 200)
        self.assertEqual(
            self.reply_calls,
            [
                (
                    self.youtube,
                    "comment-new",
                    "Final text posted from the editor.",
                )
            ],
        )
        row = self.row("comment-new")
        self.assertEqual(row["status"], "replied")
        self.assertEqual(row["final_reply"], "Final text posted from the editor.")
        self.assertEqual(row["creator_reply_id"], "youtube-reply-1")
        self.assertIn(b"Reply published", posted.data)
        self.assertNotIn(
            b"Full text for comment-new", self.client.get("/").data
        )

    def test_approves_and_posts_editor_text_in_one_action(self) -> None:
        self.client.post("/comments/comment-new/generate-reply")

        posted = self.client.post(
            "/comments/comment-new/approve-and-post",
            data={"draft_reply": "Approved and posted with the shortcut."},
            follow_redirects=True,
        )

        self.assertEqual(posted.status_code, 200)
        self.assertEqual(len(self.reply_calls), 1)
        row = self.row("comment-new")
        self.assertEqual(row["status"], "replied")
        self.assertIsNotNone(row["reply_approved_at"])
        self.assertEqual(
            row["final_reply"], "Approved and posted with the shortcut."
        )

    def test_async_forms_only_use_explicit_submit_button_overrides(self) -> None:
        script = Path("static/app.js").read_text(encoding="utf-8")

        self.assertIn('getAttribute("formaction")', script)
        self.assertIn('getAttribute("formmethod")', script)
        self.assertIn('actionPath.endsWith("/reply")', script)
        self.assertIn('refreshed.querySelector(".reply-sent")', script)
        self.assertIn('toolbar?.getBoundingClientRect().bottom', script)
        self.assertIn("cardBounds.top - visibleTop", script)
        self.assertNotIn("submitter?.formAction", script)
        self.assertNotIn("submitter?.formMethod", script)

    def test_reviews_edits_before_accepting_learned_preferences(self) -> None:
        self.client.post("/comments/comment-new/generate-reply")
        unchanged = self.client.get("/comments/comment-new")
        self.assertIn(b"Learn from Change", unchanged.data)
        self.assertIn(b"learn-button", unchanged.data)
        self.assertIn(b"disabled", unchanged.data)

        review = self.client.post(
            "/comments/comment-new/learn",
            data={"draft_reply": "Good idea. I would keep this reply short."},
            follow_redirects=True,
        )

        self.assertEqual(review.status_code, 200)
        self.assertEqual(len(self.preference_learner.calls), 1)
        comparison = self.preference_learner.calls[0]
        self.assertEqual(comparison.original_draft, "Generated reply draft.")
        self.assertEqual(
            comparison.edited_reply,
            "Good idea. I would keep this reply short.",
        )
        self.assertEqual(comparison.video_title, "PID Explained in 60 Seconds")
        self.assertEqual(comparison.category, "community_connection")
        self.assertEqual(
            comparison.classification_reason,
            "Meaningful personal impact.",
        )
        self.assertIn(b"Review the writing preferences", review.data)
        self.assertIn(b"Fred prefers shorter replies.", review.data)
        self.assertIn(b"length", review.data)
        self.assertIn(b"acknowledgement", review.data)
        self.assertNotIn(
            "Learned Preferences",
            self.creator_profile_path.read_text(encoding="utf-8"),
        )

        accepted = self.client.post(
            "/comments/comment-new/learning/accept",
            data={
                "preferences": (
                    "Fred prefers concise replies.\n"
                    "Fred acknowledges ideas before explaining."
                )
            },
            follow_redirects=True,
        )
        profile = self.creator_profile_path.read_text(encoding="utf-8")

        self.assertEqual(accepted.status_code, 200)
        self.assertIn(b"Added 2 preferences to creator.md.", accepted.data)
        self.assertIn("## Learned Preferences", profile)
        self.assertIn("- Fred prefers concise replies.", profile)
        self.assertIn(
            "- Fred acknowledges ideas before explaining.", profile
        )
        self.assertEqual(
            profile.split("## Learned Preferences", 1)[0],
            "# Fred\n\n## Style\n\n- Friendly.\n\n",
        )

    def test_does_not_learn_when_reply_matches_generated_draft(self) -> None:
        self.client.post("/comments/comment-new/generate-reply")

        response = self.client.post(
            "/comments/comment-new/learn",
            data={"draft_reply": "Generated reply draft."},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.preference_learner.calls, [])
        self.assertIn(
            b"Make a meaningful edit to the generated draft",
            response.data,
        )

    def test_legacy_draft_explains_that_regeneration_enables_learning(self) -> None:
        connection = connect_database(self.database_path)
        try:
            CommentRepository(connection).update_draft_reply(
                "comment-new", "Draft created before learning existed."
            )
        finally:
            connection.close()

        response = self.client.get("/comments/comment-new")

        self.assertIn(b"Regenerate to Enable Learning", response.data)
        self.assertIn(b"This draft predates learning", response.data)

    def test_rejects_preference_review_without_updating_profile(self) -> None:
        self.client.post("/comments/comment-new/generate-reply")
        self.client.post(
            "/comments/comment-new/learn",
            data={"draft_reply": "A significantly shorter edited reply."},
        )

        rejected = self.client.post(
            "/comments/comment-new/learning/reject",
            follow_redirects=True,
        )

        self.assertEqual(rejected.status_code, 200)
        self.assertIn(b"suggestion discarded", rejected.data)
        self.assertNotIn(
            "Learned Preferences",
            self.creator_profile_path.read_text(encoding="utf-8"),
        )

    def test_generates_all_missing_drafts_and_skips_them_on_repeat(self) -> None:
        page = self.client.get("/")
        self.assertIn(b"Generate all drafts", page.data)

        first_run = self.client.post("/inbox/generate-all")
        self.assertEqual(first_run.status_code, 302)
        self.assertEqual(len(self.reply_generator.calls), 3)
        self.assertEqual(
            self.row("comment-new")["draft_reply"],
            "Generated reply draft.",
        )
        self.assertEqual(
            self.row("comment-low")["draft_reply"],
            "Generated reply draft.",
        )
        self.assertEqual(
            self.row("comment-unclassified")["draft_reply"],
            "Generated reply draft.",
        )

        second_run = self.client.post("/inbox/generate-all")
        self.assertEqual(second_run.status_code, 302)
        self.assertEqual(len(self.reply_generator.calls), 3)

    def test_regenerates_every_active_draft(self) -> None:
        self.client.post("/inbox/generate-all")

        regenerated = self.client.post("/inbox/regenerate-all")

        self.assertEqual(regenerated.status_code, 302)
        self.assertEqual(len(self.reply_generator.calls), 6)

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
