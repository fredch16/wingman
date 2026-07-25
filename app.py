"""Minimal Flask UI for the Wingman inbox."""

import os
import sqlite3
from dataclasses import replace
from typing import Any

from dotenv import load_dotenv
from flask import Flask, abort, g, redirect, render_template, request, url_for
from google.auth.exceptions import GoogleAuthError
from googleapiclient.errors import HttpError

from classification_playground import PLAYGROUND_COMMENTS, get_playground_comment
from classification_prompt import PREVIOUS_CLASSIFICATION_PROMPT
from classification_service import ClassificationService, CommentClassification
from comment_store import connect_database
from fetch_comments import api_error_message, get_authenticated_youtube_client
from inbox_repository import CommentRepository
from production_classification import ClassificationRecord, classify_stored_comment
from reply_service import ReplyGenerationService
from video_catalog import list_stored_videos, update_video_summary
from youtube_reply import post_comment_reply


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    load_dotenv(dotenv_path=".env")
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE=os.getenv("DATABASE_PATH", "comments.db").strip() or "comments.db",
        OPENAI_MODEL=os.getenv("OPENAI_MODEL", "").strip() or "gpt-5.6-sol",
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

    app.extensions["classification_results"] = {}
    app.extensions["classification_errors"] = {}
    app.extensions["previous_classification_results"] = {}
    app.extensions["previous_classification_errors"] = {}
    app.extensions["reply_errors"] = {}
    app.extensions["reply_drafts"] = {}
    app.extensions["production_classification_errors"] = {}
    app.extensions["production_classification_results"] = {}
    app.extensions["reply_generation_errors"] = {}

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

    def reply_generation_service() -> ReplyGenerationService:
        if "reply_generation_service" not in app.extensions:
            configured_service = app.config.get("REPLY_GENERATION_SERVICE")
            app.extensions["reply_generation_service"] = (
                configured_service
                if configured_service is not None
                else ReplyGenerationService(model=app.config["OPENAI_MODEL"])
            )
        return app.extensions["reply_generation_service"]

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
        return render_template(
            "inbox.html",
            comments=comments,
            unclassified_comments=unclassified_comments,
            view=view,
            classification_errors=app.extensions[
                "production_classification_errors"
            ],
            reply_generation_errors=app.extensions[
                "reply_generation_errors"
            ],
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

    @app.get("/comments/<comment_id>")
    def comment_detail(comment_id: str) -> str:
        comment = repository().get_comment(comment_id)
        if comment is None:
            abort(404)
        return render_template(
            "comment_detail.html",
            comment=comment,
            reply_error=app.extensions["reply_errors"].get(comment_id),
            reply_draft=app.extensions["reply_drafts"].get(
                comment_id, comment.draft_reply or ""
            ),
            reply_generation_error=app.extensions[
                "reply_generation_errors"
            ].get(comment_id),
        )

    def generate_reply_for_comment(comment_id: str) -> None:
        comment = repository().get_comment(comment_id)
        if comment is None:
            abort(404)
        errors: dict[str, str] = app.extensions["reply_generation_errors"]
        try:
            draft = reply_generation_service().generate_for_comment(comment)
            repository().update_draft_reply(comment_id, draft)
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
        for comment in repository().list_active_inbox_comments():
            if not comment.draft_reply:
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

    @app.get("/videos")
    def video_contexts() -> str:
        return render_template(
            "video_contexts.html",
            videos=list_stored_videos(repository().connection),
        )

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
        reply_text = request.form.get("reply_text", "").strip()
        if not reply_text:
            app.extensions["reply_errors"][comment_id] = "Reply text cannot be empty."
            app.extensions["reply_drafts"][comment_id] = reply_text
            return redirect(url_for("comment_detail", comment_id=comment_id))

        try:
            youtube = (
                app.config["YOUTUBE_CLIENT"]
                if app.config.get("YOUTUBE_CLIENT") is not None
                else get_authenticated_youtube_client()
            )
            posted = youtube_reply_service()(youtube, comment.comment_id, reply_text)
        except HttpError as error:
            app.extensions["reply_errors"][comment_id] = api_error_message(error)
            app.extensions["reply_drafts"][comment_id] = reply_text
            app.logger.exception("YouTube reply failed for %s", comment_id)
        except (GoogleAuthError, OSError, RuntimeError, ValueError) as error:
            app.extensions["reply_errors"][comment_id] = str(error)
            app.extensions["reply_drafts"][comment_id] = reply_text
            app.logger.exception("YouTube reply failed for %s", comment_id)
        else:
            repository().mark_youtube_replied(
                comment_id=comment_id,
                reply_id=posted.reply_id,
                final_reply=posted.text,
                replied_at=posted.published_at,
            )
            app.extensions["reply_errors"].pop(comment_id, None)
            app.extensions["reply_drafts"].pop(comment_id, None)
        return redirect(url_for("comment_detail", comment_id=comment_id))

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


if __name__ == "__main__":
    create_app().run(debug=True)
