"""Minimal Flask UI for the Wingman inbox."""

import os
import sqlite3
import hashlib
import hmac
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, abort, g, jsonify, redirect, render_template, request, url_for
from google.auth.exceptions import GoogleAuthError
from googleapiclient.errors import HttpError

from wingman.playground import PLAYGROUND_COMMENTS, get_playground_comment
from wingman.ai.classification_prompt import PREVIOUS_CLASSIFICATION_PROMPT
from wingman.ai.classification_service import ClassificationService, CommentClassification
from wingman.db.comment_store import connect_database, sync_comments
from wingman.db.automation_repository import AutomationRepository
from wingman.db.automation_analytics import get_automation_analytics
from wingman.youtube.sync import api_error_message, get_authenticated_youtube_client
from wingman.db.inbox_repository import Comment, CommentRepository
from wingman.ai.learning_service import (
    ExtractedPreference,
    PreferenceComparison,
    PreferenceLearningService,
    VideoResponseComparison,
    append_learned_preferences,
    format_video_response_guidance,
    parse_review_preferences,
)
from wingman.ai.production_classification import ClassificationRecord, classify_stored_comment
from wingman.ai.reply_service import ReplyGenerationService
from wingman.db.video_catalog import (
    append_video_response_guidance,
    list_stored_videos,
    store_discovered_videos,
    update_video_summary,
)
from wingman.youtube.reply import post_comment_reply
from wingman.instagram.client import (
    InstagramAPIError,
    InstagramClient,
    post_comment_reply as post_instagram_comment_reply,
)
from wingman.instagram.sync import main as sync_instagram
from wingman.instagram.sync import account_id_from_env, comment_from_api, video_from_media
from wingman.instagram.automation import run_automation, handle_opt_in, deliver_comment
from wingman.jobs import JobManager, ProgressCallback
from wingman.reply_queue import ReplyQueue
from wingman.youtube.sync import main as sync_youtube

BULK_REPLY_PRIORITY_THRESHOLD = 0.2


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    load_dotenv(dotenv_path=".env")
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE=os.getenv("DATABASE_PATH", "comments.db").strip() or "comments.db",
        OPENAI_MODEL=os.getenv("OPENAI_MODEL", "").strip() or "gpt-5.6-sol",
        CREATOR_PROFILE=(
            os.getenv("CREATOR_CONTEXT_FILE", "").strip() or "creator.md"
        ),
        CLASSIFICATION_DRY_RUN=(
            os.getenv("WINGMAN_CLASSIFICATION_DRY_RUN", "false").strip().lower()
            not in {"0", "false", "no", "off"}
        ),
    )
    if test_config:
        app.config.update(test_config)

    def repository() -> CommentRepository:
        if "database" not in g:
            g.database = connect_database(app.config["DATABASE"])
        return CommentRepository(g.database)

    def automation_repository() -> AutomationRepository:
        if "automation_repository" not in g:
            repository()
            g.automation_repository = AutomationRepository(g.database)
        return g.automation_repository

    def with_automation(comment: Comment) -> Comment:
        if comment.draft_reply:
            return comment
        match = automation_repository().match(comment.video_id, comment.text)
        if match is None:
            return comment
        return replace(
            comment,
            automation_id=match.automation_id,
            automation_keyword=match.matched_keyword,
            automation_reply=match.default_reply,
        )

    app.extensions["classification_results"] = {}
    app.extensions["classification_errors"] = {}
    app.extensions["previous_classification_results"] = {}
    app.extensions["previous_classification_errors"] = {}
    app.extensions["reply_errors"] = {}
    app.extensions["reply_drafts"] = {}
    app.extensions["production_classification_errors"] = {}
    app.extensions["production_classification_results"] = {}
    app.extensions["reply_generation_errors"] = {}
    app.extensions["learning_proposals"] = {}
    app.extensions["learning_errors"] = {}
    app.extensions["learning_messages"] = {}
    app.extensions["video_learning_proposals"] = {}
    app.extensions["video_learning_errors"] = {}
    app.extensions["video_learning_messages"] = {}
    app.extensions["automation_error"] = None
    app.extensions["automation_message"] = None
    app.extensions["job_manager"] = JobManager()

    def send_queued_reply(platform: str, comment_id: str, reply_text: str):
        if platform == "instagram":
            return instagram_reply_service()(
                instagram_client(), comment_id, reply_text
            )
        youtube = (
            app.config["YOUTUBE_CLIENT"]
            if app.config.get("YOUTUBE_CLIENT") is not None
            else get_authenticated_youtube_client()
        )
        return youtube_reply_service()(youtube, comment_id, reply_text)

    app.extensions["reply_queue"] = ReplyQueue(
        app.config["DATABASE"], send_queued_reply
    )
    if app.config.get("START_REPLY_WORKER", True) and (
        not app.config.get("TESTING") or app.config.get("ASYNC_POSTS")
    ):
        app.extensions["reply_queue"].mark_interrupted()
        app.extensions["reply_queue"].start()

    def classification_service() -> ClassificationService:
        if "classification_service" not in app.extensions:
            configured_service = app.config.get("CLASSIFICATION_SERVICE")
            app.extensions["classification_service"] = (
                configured_service
                if configured_service is not None
                else ClassificationService(model=app.config["OPENAI_MODEL"])
            )
        return app.extensions["classification_service"]

    def youtube_reply_service():
        configured_service = app.config.get("YOUTUBE_REPLY_SERVICE")
        if configured_service is not None:
            return configured_service
        return post_comment_reply

    def instagram_client() -> InstagramClient:
        configured_client = app.config.get("INSTAGRAM_CLIENT")
        if configured_client is not None:
            return configured_client
        return InstagramClient(
            os.getenv("INSTAGRAM_ACCESS_TOKEN", ""),
            os.getenv("INSTAGRAM_GRAPH_API_VERSION", "v25.0"),
        )

    def instagram_reply_service():
        configured_service = app.config.get("INSTAGRAM_REPLY_SERVICE")
        if configured_service is not None:
            return configured_service
        return post_instagram_comment_reply

    def reply_generation_service() -> ReplyGenerationService:
        if "reply_generation_service" not in app.extensions:
            configured_service = app.config.get("REPLY_GENERATION_SERVICE")
            app.extensions["reply_generation_service"] = (
                configured_service
                if configured_service is not None
                else ReplyGenerationService(model=app.config["OPENAI_MODEL"])
            )
        return app.extensions["reply_generation_service"]

    def preference_learning_service() -> PreferenceLearningService:
        if "preference_learning_service" not in app.extensions:
            configured_service = app.config.get("PREFERENCE_LEARNING_SERVICE")
            app.extensions["preference_learning_service"] = (
                configured_service
                if configured_service is not None
                else PreferenceLearningService(model=app.config["OPENAI_MODEL"])
            )
        return app.extensions["preference_learning_service"]

    @app.teardown_appcontext
    def close_database(_error: BaseException | None) -> None:
        connection: sqlite3.Connection | None = g.pop("database", None)
        if connection is not None:
            connection.close()

    @app.get("/")
    def inbox() -> str:
        view = request.args.get("view", "inbox")
        if view not in {"inbox", "ignored"}:
            abort(404)
        classified_by_id = {
            comment.comment_id: comment
            for comment in repository().list_classified_inbox_comments()
        }
        dry_run_results: dict[str, ClassificationRecord] = app.extensions[
            "production_classification_results"
        ]
        for comment_id, record in dry_run_results.items():
            comment = repository().get_comment(comment_id)
            if comment is None:
                continue
            result = record.result
            classified_by_id[comment_id] = replace(
                comment,
                category=result.category,
                priority=result.priority,
                reply_worthy=result.reply_worthy,
                needs_research=result.needs_research,
                classification_reason=result.reason,
                classified_at=record.classified_at,
                classification_model=record.classification_model,
                classification_version=record.classification_version,
            )
        if view == "ignored":
            comments = repository().list_inbox_comments("ignored")
            unclassified_comments = []
        else:
            comments = sorted(
                classified_by_id.values(),
                key=lambda comment: comment.published_at,
                reverse=True,
            )
            comments.sort(
                key=lambda comment: comment.priority or 0.0,
                reverse=True,
            )
            unclassified_comments = [
                comment
                for comment in repository().list_unclassified_inbox_comments()
                if comment.comment_id not in dry_run_results
            ]
        comments = [with_automation(comment) for comment in comments]
        unclassified_comments = [
            with_automation(comment) for comment in unclassified_comments
        ]
        return render_template(
            "inbox.html",
            comments=comments,
            unclassified_comments=unclassified_comments,
            failed_replies=app.extensions["reply_queue"].failed(),
            view=view,
            classification_errors=app.extensions[
                "production_classification_errors"
            ],
            reply_generation_errors=app.extensions[
                "reply_generation_errors"
            ],
            learning_proposals=app.extensions["learning_proposals"],
            learning_errors=app.extensions["learning_errors"],
            learning_messages=app.extensions["learning_messages"],
            video_learning_proposals=app.extensions["video_learning_proposals"],
            video_learning_errors=app.extensions["video_learning_errors"],
            video_learning_messages=app.extensions["video_learning_messages"],
            classification_dry_run=app.config["CLASSIFICATION_DRY_RUN"],
        )

    def classify_production_comment(comment_id: str) -> None:
        comment = repository().get_comment(comment_id)
        if comment is None:
            abort(404)
        if (
            comment.status == "replied"
            or comment.has_creator_reply
            or comment.is_ignored
        ):
            abort(409)
        errors: dict[str, str] = app.extensions[
            "production_classification_errors"
        ]
        try:
            record = classify_stored_comment(
                repository(),
                classification_service(),
                comment_id,
                persist=not app.config["CLASSIFICATION_DRY_RUN"],
            )
            if app.config["CLASSIFICATION_DRY_RUN"]:
                app.extensions["production_classification_results"][
                    comment_id
                ] = record
            errors.pop(comment_id, None)
        except Exception as error:
            errors[comment_id] = str(error)
            app.logger.exception(
                "Production classification failed for %s", comment_id
            )

    @app.post("/comments/<comment_id>/classify")
    def classify_comment(comment_id: str):
        classify_production_comment(comment_id)
        return redirect(url_for("inbox"))

    @app.post("/inbox/classify-top-10")
    def classify_top_10():
        for comment in repository().list_unclassified_inbox_comments(limit=10):
            classify_production_comment(comment.comment_id)
        return redirect(url_for("inbox"))

    @app.post("/inbox/classify-all")
    def classify_all_unclassified():
        for comment in repository().list_unclassified_inbox_comments():
            classify_production_comment(comment.comment_id)
        return redirect(url_for("inbox"))

    @app.post("/inbox/reclassify-all")
    def reclassify_all():
        for comment in repository().list_active_inbox_comments():
            classify_production_comment(comment.comment_id)
        return redirect(url_for("inbox"))

    def start_sync_job(platform: str):
        commands = {
            "youtube": (
                "Refetch YouTube",
                app.config.get("YOUTUBE_SYNC_COMMAND", sync_youtube),
            ),
            "instagram": (
                "Refetch Instagram",
                app.config.get("INSTAGRAM_SYNC_COMMAND", sync_instagram),
            ),
        }
        selected = list(commands) if platform == "all" else [platform]
        if any(name not in commands for name in selected):
            abort(404)

        def worker(update: ProgressCallback) -> None:
            for index, name in enumerate(selected, start=1):
                label, command = commands[name]
                update(index - 1, len(selected), label)
                status = command(["--refresh"])
                if status:
                    raise RuntimeError(f"{label} failed with status {status}")
                update(index, len(selected), f"{label} complete")

        label = "Refetch all platforms" if platform == "all" else commands[platform][0]
        job = app.extensions["job_manager"].start(label, len(selected), worker)
        return jsonify(app.extensions["job_manager"].get(job.job_id)), 202

    @app.post("/jobs/sync/<platform>")
    def run_sync_job(platform: str):
        return start_sync_job(platform)

    @app.post("/jobs/classify")
    def run_classification_job():
        with closing(connect_database(app.config["DATABASE"])) as connection:
            comment_ids = [
                comment.comment_id
                for comment in CommentRepository(
                    connection
                ).list_unclassified_inbox_comments()
            ]

        def worker(update: ProgressCallback) -> None:
            with closing(connect_database(app.config["DATABASE"])) as connection:
                job_repository = CommentRepository(connection)
                for index, comment_id in enumerate(comment_ids, start=1):
                    comment = job_repository.get_comment(comment_id)
                    update(
                        index - 1,
                        len(comment_ids),
                        f"Classifying {comment.author_display_name if comment else comment_id}",
                    )
                    classify_stored_comment(
                        job_repository,
                        classification_service(),
                        comment_id,
                        persist=not app.config["CLASSIFICATION_DRY_RUN"],
                    )
                    update(index, len(comment_ids), "Classification saved")

        job = app.extensions["job_manager"].start(
            "Classify comments", len(comment_ids), worker
        )
        return jsonify(app.extensions["job_manager"].get(job.job_id)), 202

    @app.post("/jobs/generate")
    def run_generation_job():
        rated_only = request.form.get("rated_only") == "1"
        with closing(connect_database(app.config["DATABASE"])) as connection:
            candidates = [
                comment
                for comment in CommentRepository(connection).list_active_inbox_comments()
                if not comment.draft_reply
                and (
                    not rated_only
                    or (
                        comment.priority is not None
                        and comment.priority > BULK_REPLY_PRIORITY_THRESHOLD
                    )
                )
            ]
        comment_ids = [comment.comment_id for comment in candidates]

        def worker(update: ProgressCallback) -> None:
            with closing(connect_database(app.config["DATABASE"])) as connection:
                job_repository = CommentRepository(connection)
                for index, comment_id in enumerate(comment_ids, start=1):
                    comment = job_repository.get_comment(comment_id)
                    if comment is None:
                        continue
                    update(
                        index - 1,
                        len(comment_ids),
                        f"Drafting for {comment.author_display_name}",
                    )
                    draft = reply_generation_service().generate_for_comment(comment)
                    job_repository.save_generated_reply(comment_id, draft)
                    update(index, len(comment_ids), "Draft saved")

        job = app.extensions["job_manager"].start(
            "Generate replies", len(comment_ids), worker
        )
        return jsonify(app.extensions["job_manager"].get(job.job_id)), 202

    @app.get("/jobs/<job_id>")
    def job_status(job_id: str):
        job = app.extensions["job_manager"].get(job_id)
        if job is None:
            abort(404)
        return jsonify(job)

    @app.get("/comments/<comment_id>")
    def comment_detail(comment_id: str) -> str:
        comment = repository().get_comment(comment_id)
        if comment is None:
            abort(404)
        comment = with_automation(comment)
        queued_status = app.extensions["reply_queue"].status(comment_id)
        return render_template(
            "comment_detail.html",
            comment=comment,
            reply_error=(queued_status or {}).get("error")
            or app.extensions["reply_errors"].get(comment_id),
            reply_draft=app.extensions["reply_drafts"].get(
                comment_id, comment.draft_reply or ""
            ),
            reply_generation_error=app.extensions[
                "reply_generation_errors"
            ].get(comment_id),
            learning_proposal=app.extensions["learning_proposals"].get(
                comment_id
            ),
            learning_error=app.extensions["learning_errors"].get(comment_id),
            learning_message=app.extensions["learning_messages"].get(comment_id),
            video_learning_proposal=app.extensions[
                "video_learning_proposals"
            ].get(comment_id),
            video_learning_error=app.extensions["video_learning_errors"].get(
                comment_id
            ),
            video_learning_message=app.extensions[
                "video_learning_messages"
            ].get(comment_id),
        )

    def generate_reply_for_comment(comment_id: str) -> None:
        comment = repository().get_comment(comment_id)
        if comment is None:
            abort(404)
        errors: dict[str, str] = app.extensions["reply_generation_errors"]
        try:
            draft = reply_generation_service().generate_for_comment(comment)
            repository().save_generated_reply(comment_id, draft)
            errors.pop(comment_id, None)
        except Exception as error:
            errors[comment_id] = str(error)
            app.logger.exception("Reply generation failed for %s", comment_id)

    @app.post("/comments/<comment_id>/generate-reply")
    def generate_reply(comment_id: str):
        generate_reply_for_comment(comment_id)
        return redirect(url_for("comment_detail", comment_id=comment_id))

    @app.post("/inbox/generate-all")
    def generate_all_replies():
        rated_only = request.form.get("rated_only") == "1"
        for comment in repository().list_active_inbox_comments():
            if comment.draft_reply:
                continue
            if rated_only and (
                comment.priority is None
                or comment.priority <= BULK_REPLY_PRIORITY_THRESHOLD
            ):
                continue
            generate_reply_for_comment(comment.comment_id)
        return redirect(url_for("inbox"))

    @app.post("/inbox/regenerate-all")
    def regenerate_all_replies():
        for comment in repository().list_active_inbox_comments():
            if (
                comment.priority is not None
                and comment.priority > BULK_REPLY_PRIORITY_THRESHOLD
            ):
                generate_reply_for_comment(comment.comment_id)
        return redirect(url_for("inbox"))

    @app.post("/comments/<comment_id>/draft-reply")
    def save_draft_reply(comment_id: str):
        if repository().get_comment(comment_id) is None:
            abort(404)
        draft = request.form.get("draft_reply", "").strip()
        if not repository().update_draft_reply(comment_id, draft or None):
            abort(404)
        return redirect(url_for("comment_detail", comment_id=comment_id))

    @app.post("/comments/<comment_id>/approve-reply")
    def approve_reply(comment_id: str):
        edited_draft = request.form.get("draft_reply")
        if edited_draft is not None:
            if not repository().update_draft_reply(
                comment_id, edited_draft.strip() or None
            ):
                abort(404)
        if not repository().approve_draft_reply(comment_id):
            abort(400)
        return redirect(url_for("comment_detail", comment_id=comment_id))

    @app.post("/comments/<comment_id>/approve-and-post")
    def approve_and_post_reply(comment_id: str):
        edited_draft = request.form.get("draft_reply", "").strip()
        if not edited_draft:
            abort(400)
        if not repository().update_draft_reply(comment_id, edited_draft):
            abort(404)
        if not repository().approve_draft_reply(comment_id):
            abort(400)
        return post_reply(comment_id)

    @app.post("/comments/<comment_id>/learn")
    def learn_from_change(comment_id: str):
        comment = repository().get_comment(comment_id)
        if comment is None:
            abort(404)
        edited_reply = request.form.get("draft_reply", "").strip()
        original_draft = (comment.original_draft_reply or "").strip()
        errors: dict[str, str] = app.extensions["learning_errors"]
        messages: dict[str, str] = app.extensions["learning_messages"]
        proposals: dict[str, list[ExtractedPreference]] = app.extensions[
            "learning_proposals"
        ]
        if not original_draft or edited_reply == original_draft:
            errors[comment_id] = (
                "Make a meaningful edit to the generated draft before learning."
            )
            return redirect(url_for("comment_detail", comment_id=comment_id))

        repository().update_draft_reply(comment_id, edited_reply)
        try:
            preferences = preference_learning_service().extract_preferences(
                PreferenceComparison(
                    original_draft=original_draft,
                    edited_reply=edited_reply,
                    comment_text=comment.text,
                    video_title=comment.video_title,
                    video_summary=comment.video_summary,
                    category=comment.category,
                    classification_reason=comment.classification_reason,
                )
            )
            proposals[comment_id] = preferences
            errors.pop(comment_id, None)
            messages.pop(comment_id, None)
        except Exception as error:
            errors[comment_id] = str(error)
            app.logger.exception(
                "Preference extraction failed for %s", comment_id
            )
        return redirect(url_for("comment_detail", comment_id=comment_id))

    @app.post("/comments/<comment_id>/learning/accept")
    def accept_learned_preferences(comment_id: str):
        if repository().get_comment(comment_id) is None:
            abort(404)
        proposals: dict[str, list[ExtractedPreference]] = app.extensions[
            "learning_proposals"
        ]
        if comment_id not in proposals:
            abort(409)
        preferences = parse_review_preferences(
            request.form.get("preferences", "")
        )
        if not preferences:
            app.extensions["learning_errors"][comment_id] = (
                "Keep at least one preference before accepting."
            )
            return redirect(url_for("comment_detail", comment_id=comment_id))
        try:
            result = append_learned_preferences(
                Path(app.config["CREATOR_PROFILE"]),
                preferences,
            )
        except (OSError, ValueError) as error:
            app.extensions["learning_errors"][comment_id] = str(error)
        else:
            if result.added:
                message = (
                    f"Added {len(result.added)} preference"
                    f"{'' if len(result.added) == 1 else 's'} to creator.md."
                )
            else:
                message = "Those preferences already exist in creator.md."
            app.extensions["learning_messages"][comment_id] = message
            app.extensions["learning_errors"].pop(comment_id, None)
            proposals.pop(comment_id, None)
        return redirect(url_for("comment_detail", comment_id=comment_id))

    @app.post("/comments/<comment_id>/learning/reject")
    def reject_learned_preferences(comment_id: str):
        if repository().get_comment(comment_id) is None:
            abort(404)
        app.extensions["learning_proposals"].pop(comment_id, None)
        app.extensions["learning_errors"].pop(comment_id, None)
        app.extensions["learning_messages"][comment_id] = (
            "Learned preference suggestion discarded."
        )
        return redirect(url_for("comment_detail", comment_id=comment_id))

    @app.post("/comments/<comment_id>/learn-video-context")
    def learn_video_context(comment_id: str):
        comment = repository().get_comment(comment_id)
        if comment is None:
            abort(404)
        creator_reply = request.form.get("draft_reply", "").strip()
        if not creator_reply:
            app.extensions["video_learning_errors"][comment_id] = (
                "Write a reply before learning video-specific guidance."
            )
            return redirect(url_for("comment_detail", comment_id=comment_id))
        repository().update_draft_reply(comment_id, creator_reply)
        try:
            guidance = (
                preference_learning_service().extract_video_response_guidance(
                    VideoResponseComparison(
                        comment_text=comment.text,
                        creator_reply=creator_reply,
                        video_title=comment.video_title,
                        video_summary=comment.video_summary,
                    )
                )
            )
            app.extensions["video_learning_proposals"][comment_id] = (
                format_video_response_guidance(guidance)
            )
            app.extensions["video_learning_errors"].pop(comment_id, None)
            app.extensions["video_learning_messages"].pop(comment_id, None)
        except Exception as error:
            app.extensions["video_learning_errors"][comment_id] = str(error)
            app.logger.exception(
                "Video context learning failed for %s", comment_id
            )
        return redirect(url_for("comment_detail", comment_id=comment_id))

    @app.post("/comments/<comment_id>/video-learning/accept")
    def accept_video_context_learning(comment_id: str):
        comment = repository().get_comment(comment_id)
        if comment is None:
            abort(404)
        proposals: dict[str, str] = app.extensions[
            "video_learning_proposals"
        ]
        if comment_id not in proposals:
            abort(409)
        guidance = request.form.get("guidance", "").strip()
        if not guidance:
            app.extensions["video_learning_errors"][comment_id] = (
                "Keep response guidance before accepting."
            )
            return redirect(url_for("comment_detail", comment_id=comment_id))
        try:
            if not append_video_response_guidance(
                repository().connection,
                comment.video_id,
                guidance,
            ):
                abort(404)
        except ValueError as error:
            app.extensions["video_learning_errors"][comment_id] = str(error)
        else:
            proposals.pop(comment_id, None)
            app.extensions["video_learning_errors"].pop(comment_id, None)
            app.extensions["video_learning_messages"][comment_id] = (
                "Added response guidance to this video’s context."
            )
        return redirect(url_for("comment_detail", comment_id=comment_id))

    @app.post("/comments/<comment_id>/video-learning/reject")
    def reject_video_context_learning(comment_id: str):
        if repository().get_comment(comment_id) is None:
            abort(404)
        app.extensions["video_learning_proposals"].pop(comment_id, None)
        app.extensions["video_learning_errors"].pop(comment_id, None)
        app.extensions["video_learning_messages"][comment_id] = (
            "Video response guidance discarded."
        )
        return redirect(url_for("comment_detail", comment_id=comment_id))

    @app.get("/videos")
    def video_contexts() -> str:
        return render_template(
            "video_contexts.html",
            videos=list_stored_videos(repository().connection),
        )

    @app.get("/automations")
    def automations() -> str:
        return render_template(
            "automations.html",
            automations=automation_repository().list_all(),
            videos=list_stored_videos(repository().connection),
            automation_error=app.extensions["automation_error"],
            automation_message=app.extensions["automation_message"],
            instagram_webhook_configured=bool(
                os.getenv("INSTAGRAM_APP_SECRET") and os.getenv("INSTAGRAM_WEBHOOK_VERIFY_TOKEN")
            ),
            deliveries=repository().connection.execute(
                "SELECT comment_id, status, error FROM automation_deliveries ORDER BY created_at DESC LIMIT 25"
            ).fetchall(),
        )

    @app.get("/analytics")
    def automation_analytics() -> str:
        return render_template(
            "analytics.html",
            analytics=get_automation_analytics(repository().connection),
        )

    def automation_messages_from_form() -> tuple[str, str]:
        """Use the unified editor, while accepting legacy form submissions."""
        if "reply_options" not in request.form:
            return request.form.get("default_reply", ""), request.form.get("public_reply_variants", "")
        if request.form.get("mode") == "youtube_reply":
            # Existing YouTube prefills contain intentional multi-line replies.
            return request.form["reply_options"].strip(), ""
        replies = [line.strip() for line in request.form["reply_options"].splitlines() if line.strip()]
        return (replies[0], "\n".join(replies[1:])) if replies else ("", "")

    def automation_links_from_form() -> str:
        """Serialize labeled link rows for the existing repository parser."""
        if "link_label[]" not in request.form and "link_url[]" not in request.form:
            return request.form.get("followup_links", "")
        labels = request.form.getlist("link_label[]")
        urls = request.form.getlist("link_url[]")
        if len(labels) != len(urls):
            raise ValueError("Each link needs both a button label and URL.")
        return "\n".join(
            f"{label} | {url}" for label, url in zip(labels, urls)
            if label.strip() or url.strip()
        )

    @app.post("/automations")
    def create_automation():
        try:
            first_reply, extra_replies = automation_messages_from_form()
            automation_repository().create(
                request.form.get("video_id", "").strip(),
                request.form.get("keywords", ""),
                first_reply,
                mode=request.form.get("mode", "prefill"),
                initial_dm=request.form.get("initial_dm", ""),
                followup_dm=request.form.get("followup_dm", ""),
                match_type=request.form.get("match_type", "contains"),
                public_reply_variants=extra_replies,
                opt_in_button_label=request.form.get("opt_in_button_label", "Yes please"),
                followup_links=automation_links_from_form(),
            )
            app.extensions["automation_error"] = None
        except ValueError as error:
            app.extensions["automation_error"] = str(error)
        return redirect(url_for("automations"))

    @app.post("/automations/<int:automation_id>/update")
    def update_automation(automation_id: int):
        try:
            first_reply, extra_replies = automation_messages_from_form()
            updated = automation_repository().update(
                automation_id,
                request.form.get("keywords", ""),
                first_reply,
                mode=request.form.get("mode", "prefill"),
                initial_dm=request.form.get("initial_dm", ""),
                followup_dm=request.form.get("followup_dm", ""),
                match_type=request.form.get("match_type", "contains"),
                public_reply_variants=extra_replies,
                opt_in_button_label=request.form.get("opt_in_button_label", "Yes please"),
                followup_links=automation_links_from_form(),
            )
            if not updated:
                abort(404)
            app.extensions["automation_error"] = None
        except ValueError as error:
            app.extensions["automation_error"] = str(error)
        return redirect(url_for("automations"))

    @app.post("/automations/<int:automation_id>/toggle")
    def toggle_automation(automation_id: int):
        is_enabled = request.form.get("is_enabled") == "true"
        if not automation_repository().set_enabled(automation_id, is_enabled):
            abort(404)
        return redirect(url_for("automations"))

    @app.post("/automations/<int:automation_id>/run")
    def run_instagram_automation(automation_id: int):
        automation = automation_repository().get(automation_id)
        if automation is None:
            abort(404)
        if automation.mode != "instagram_dm" or not automation.is_enabled:
            abort(400)
        try:
            client = instagram_client()
            account_id, _ = account_id_from_env(client)
            result = run_automation(automation_repository(), client, account_id, automation)
            app.extensions["automation_message"] = (
                f"Matched {result.eligible}; sent {result.sent}; "
                f"skipped {result.skipped}; failed {result.failed}."
            )
            app.extensions["automation_error"] = None
        except (InstagramAPIError, ValueError) as error:
            app.extensions["automation_error"] = str(error)
        return redirect(url_for("automations"))

    @app.get("/webhooks/instagram")
    def verify_instagram_webhook():
        token = os.getenv("INSTAGRAM_WEBHOOK_VERIFY_TOKEN", "")
        if not token or request.args.get("hub.mode") != "subscribe":
            abort(403)
        if not hmac.compare_digest(request.args.get("hub.verify_token", ""), token):
            abort(403)
        return request.args.get("hub.challenge", "")

    @app.post("/webhooks/instagram")
    def instagram_webhook():
        secret = os.getenv("INSTAGRAM_APP_SECRET", "")
        if not secret:
            abort(503)
        signature = request.headers.get("X-Hub-Signature-256", "")
        digest = hmac.new(secret.encode(), request.get_data(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, f"sha256={digest}"):
            app.logger.warning(
                "Rejected Instagram webhook: invalid signature "
                "(sha256 header present=%s, sha1 header present=%s, sha256 format=%s)",
                bool(signature),
                bool(request.headers.get("X-Hub-Signature")),
                signature.startswith("sha256=") and len(signature) == 71,
            )
            abort(403)
        data = request.get_json(silent=True) or {}
        client = instagram_client()
        account_id, creator_username = account_id_from_env(client)
        for entry in data.get("entry", []):
            app.logger.warning(
                "Instagram webhook entry: %d comment changes, %d messaging events",
                len(entry.get("changes", [])), len(entry.get("messaging", [])),
            )
            for change in entry.get("changes", []):
                if change.get("field") != "comments":
                    continue
                value = change.get("value") or {}
                comment_id = str(value.get("id", ""))
                media_id = str((value.get("media") or {}).get("id", ""))
                if not comment_id or not media_id or value.get("parent_id"):
                    continue
                try:
                    full = client.get(
                        comment_id,
                        fields="id,text,timestamp,parent_id,from,username,like_count",
                    )
                except InstagramAPIError:
                    app.logger.exception("Could not load Instagram comment %s", comment_id)
                    return jsonify({"ok": False, "error": "Comment fetch failed"}), 503
                if full.get("parent_id"):
                    continue
                author = full.get("from") or value.get("from") or {}
                full["from"] = author
                full["username"] = full.get("username") or value.get("username")
                author_id = str(author.get("id") or "") if isinstance(author, dict) else ""
                author_username = str(
                    (author.get("username") if isinstance(author, dict) else "")
                    or full.get("username") or ""
                )
                if author_id == account_id or (
                    creator_username and author_username.casefold().lstrip("@")
                    == creator_username.casefold().lstrip("@")
                ):
                    continue
                matching_rules = [
                    rule for rule in automation_repository().list_all()
                    if rule.video_id == media_id and rule.mode == "instagram_dm"
                    and rule.is_enabled and automation_repository().match_delivery(
                        rule, str(full.get("text", ""))
                    )
                ]
                if not repository().connection.execute(
                    "SELECT 1 FROM videos WHERE video_id = ?", (media_id,)
                ).fetchone():
                    try:
                        media = client.get(
                            media_id,
                            fields="id,caption,timestamp,thumbnail_url,media_url,permalink",
                        )
                        store_discovered_videos(repository().connection, [video_from_media(media)])
                    except InstagramAPIError:
                        app.logger.warning("Could not load Instagram media %s; using its ID as title", media_id)
                sync_comments(repository().connection, [comment_from_api(full, media_id)])
                if not matching_rules:
                    app.logger.info("Instagram comment %s added to inbox", comment_id)
                    continue
                existing_delivery = automation_repository().delivery(comment_id)
                if existing_delivery:
                    if existing_delivery["status"] in {"awaiting_opt_in", "completed"}:
                        repository().connection.execute(
                            "UPDATE comments SET is_ignored = 1 WHERE comment_id = ?",
                            (comment_id,),
                        )
                        repository().connection.commit()
                    continue
                comment = {
                    "comment_id": comment_id,
                    "text": str(full.get("text", "")),
                    "published_at": str(full.get("timestamp", "")),
                    "author_channel_id": author_id,
                }
                for rule in matching_rules:
                    outcome = deliver_comment(
                        automation_repository(), client, account_id, rule, comment
                    )
                    app.logger.warning(
                        "Instagram automation %s comment %s: %s",
                        rule.automation_id, comment_id, outcome,
                    )
                    if outcome == "sent":
                        repository().connection.execute(
                            "UPDATE comments SET is_ignored = 1 WHERE comment_id = ?",
                            (comment_id,),
                        )
                        repository().connection.commit()
                    if outcome != "skipped":
                        break
            for event in entry.get("messaging", []):
                sender = str(event.get("sender", {}).get("id", ""))
                message = event.get("message") or {}
                payload = (message.get("quick_reply") or {}).get("payload")
                payload = payload or (event.get("postback") or {}).get("payload", "")
                if sender and payload:
                    handled = handle_opt_in(
                        automation_repository(), client, account_id, sender, payload
                    )
                    app.logger.warning("Instagram opt-in payload handled: %s", handled)
        return jsonify({"ok": True})

    @app.post("/automations/<int:automation_id>/delete")
    def delete_automation(automation_id: int):
        if not automation_repository().delete(automation_id):
            abort(404)
        return redirect(url_for("automations"))

    @app.post("/videos/<video_id>/summary")
    def save_video_summary(video_id: str):
        summary = request.form.get("summary", "")
        if not update_video_summary(repository().connection, video_id, summary):
            abort(404)
        return redirect(url_for("video_contexts"))

    @app.post("/comments/<comment_id>/ignore")
    def toggle_ignored(comment_id: str):
        comment = repository().get_comment(comment_id)
        if comment is None:
            abort(404)
        if comment.is_ignored:
            repository().unignore(comment_id)
        else:
            repository().mark_ignored(comment_id)
        return redirect(url_for("comment_detail", comment_id=comment_id))

    @app.post("/comments/<comment_id>/research")
    def toggle_research(comment_id: str):
        comment = repository().get_comment(comment_id)
        if comment is None:
            abort(404)
        repository().mark_needs_research(
            comment_id, needs_research=not comment.needs_research
        )
        return redirect(url_for("comment_detail", comment_id=comment_id))

    @app.post("/comments/<comment_id>/replied")
    def mark_replied(comment_id: str):
        if not repository().mark_replied(comment_id):
            abort(404)
        return redirect(url_for("comment_detail", comment_id=comment_id))

    @app.post("/comments/<comment_id>/reply")
    def post_reply(comment_id: str):
        comment = repository().get_comment(comment_id)
        if comment is None:
            abort(404)
        if comment.status == "replied" or comment.has_creator_reply:
            abort(409)
        reply_text = (
            request.form.get("reply_text")
            or request.form.get("draft_reply")
            or ""
        ).strip()
        if not reply_text:
            app.extensions["reply_errors"][comment_id] = "Reply text cannot be empty."
            app.extensions["reply_drafts"][comment_id] = reply_text
            return redirect(url_for("comment_detail", comment_id=comment_id))

        if not app.config.get("TESTING") or app.config.get("ASYNC_POSTS"):
            queue: ReplyQueue = app.extensions["reply_queue"]
            if not queue.enqueue(comment_id, comment.platform, reply_text):
                abort(409, description="This reply is already queued or posted.")
            if request.accept_mimetypes.best == "application/json":
                return jsonify({"status": "queued", "comment_id": comment_id}), 202
            return redirect(url_for("comment_detail", comment_id=comment_id))

        try:
            if comment.platform == "instagram":
                posted = instagram_reply_service()(
                    instagram_client(), comment.comment_id, reply_text
                )
            else:
                youtube = (
                    app.config["YOUTUBE_CLIENT"]
                    if app.config.get("YOUTUBE_CLIENT") is not None
                    else get_authenticated_youtube_client()
                )
                posted = youtube_reply_service()(
                    youtube, comment.comment_id, reply_text
                )
        except HttpError as error:
            app.extensions["reply_errors"][comment_id] = api_error_message(error)
            app.extensions["reply_drafts"][comment_id] = reply_text
            app.logger.exception("YouTube reply failed for %s", comment_id)
        except (
            GoogleAuthError,
            InstagramAPIError,
            OSError,
            RuntimeError,
            ValueError,
        ) as error:
            app.extensions["reply_errors"][comment_id] = str(error)
            app.extensions["reply_drafts"][comment_id] = reply_text
            app.logger.exception("%s reply failed for %s", comment.platform, comment_id)
        else:
            repository().mark_platform_replied(
                comment_id=comment_id,
                reply_id=posted.reply_id,
                final_reply=posted.text,
                replied_at=posted.published_at,
            )
            app.extensions["reply_errors"].pop(comment_id, None)
            app.extensions["reply_drafts"].pop(comment_id, None)
        return redirect(url_for("comment_detail", comment_id=comment_id))

    @app.get("/comments/<comment_id>/reply-status")
    def reply_status(comment_id: str):
        if repository().get_comment(comment_id) is None:
            abort(404)
        status = app.extensions["reply_queue"].status(comment_id)
        if status is None:
            abort(404)
        return jsonify(status)

    @app.get("/classification-playground")
    def classification_playground() -> str:
        return render_template(
            "classification_playground.html",
            comments=PLAYGROUND_COMMENTS,
            results=app.extensions["classification_results"],
            errors=app.extensions["classification_errors"],
            previous_results=app.extensions["previous_classification_results"],
            previous_errors=app.extensions["previous_classification_errors"],
            model=app.config["OPENAI_MODEL"],
        )

    def classify_playground_comment(
        comment_id: str,
        preserve_current_as_previous: bool = True,
    ) -> None:
        comment = get_playground_comment(comment_id)
        if comment is None:
            abort(404)
        results: dict[str, CommentClassification] = app.extensions[
            "classification_results"
        ]
        errors: dict[str, str] = app.extensions["classification_errors"]
        previous_results: dict[str, CommentClassification] = app.extensions[
            "previous_classification_results"
        ]
        if preserve_current_as_previous and comment_id in results:
            previous_results[comment_id] = results[comment_id]
        try:
            results[comment_id] = classification_service().classify(
                comment.text,
                video_title=comment.video_title,
            )
            errors.pop(comment_id, None)
        except Exception as error:
            results.pop(comment_id, None)
            errors[comment_id] = str(error)
            app.logger.exception("Classification failed for %s", comment_id)

    @app.post("/classification-playground/classify/<comment_id>")
    def classify_one(comment_id: str):
        classify_playground_comment(comment_id)
        return redirect(url_for("classification_playground"))

    @app.post("/classification-playground/classify-all")
    def classify_all():
        previous_results: dict[str, CommentClassification] = app.extensions[
            "previous_classification_results"
        ]
        previous_errors: dict[str, str] = app.extensions[
            "previous_classification_errors"
        ]
        for comment in PLAYGROUND_COMMENTS:
            try:
                previous_results[comment.comment_id] = (
                    classification_service().classify(
                        comment.text,
                        prompt=PREVIOUS_CLASSIFICATION_PROMPT,
                        video_title=comment.video_title,
                    )
                )
                previous_errors.pop(comment.comment_id, None)
            except Exception as error:
                previous_results.pop(comment.comment_id, None)
                previous_errors[comment.comment_id] = str(error)
                app.logger.exception(
                    "Previous-prompt classification failed for %s",
                    comment.comment_id,
                )
            classify_playground_comment(
                comment.comment_id,
                preserve_current_as_previous=False,
            )
        return redirect(url_for("classification_playground"))

    return app


def main() -> None:
    create_app().run(debug=True)


if __name__ == "__main__":
    main()
