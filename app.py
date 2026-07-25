"""Minimal Flask UI for the Wingman inbox."""

import os
import sqlite3
from typing import Any

from dotenv import load_dotenv
from flask import Flask, abort, g, redirect, render_template, request, url_for
from google.auth.exceptions import GoogleAuthError
from googleapiclient.errors import HttpError

from classification_playground import PLAYGROUND_COMMENTS, get_playground_comment
from classification_service import ClassificationService, CommentClassification
from comment_store import connect_database
from fetch_comments import api_error_message, get_authenticated_youtube_client
from inbox_repository import CommentRepository
from youtube_reply import post_comment_reply

INBOX_FILTERS = {"all", "new", "needs_research", "ignored", "replied"}


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    load_dotenv(dotenv_path=".env")
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE=os.getenv("DATABASE_PATH", "comments.db").strip() or "comments.db",
        OPENAI_MODEL=os.getenv("OPENAI_MODEL", "").strip() or "gpt-5.6-sol",
    )
    if test_config:
        app.config.update(test_config)

    def repository() -> CommentRepository:
        if "database" not in g:
            g.database = connect_database(app.config["DATABASE"])
        return CommentRepository(g.database)

    app.extensions["classification_results"] = {}
    app.extensions["classification_errors"] = {}
    app.extensions["reply_errors"] = {}
    app.extensions["reply_drafts"] = {}

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

    @app.teardown_appcontext
    def close_database(_error: BaseException | None) -> None:
        connection: sqlite3.Connection | None = g.pop("database", None)
        if connection is not None:
            connection.close()

    @app.get("/")
    def inbox() -> str:
        selected_filter = request.args.get("filter", "all")
        if selected_filter not in INBOX_FILTERS:
            abort(400)
        return render_template(
            "inbox.html",
            comments=repository().list_inbox_comments(selected_filter),
            selected_filter=selected_filter,
        )

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
        )

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
            model=app.config["OPENAI_MODEL"],
        )

    def classify_playground_comment(comment_id: str) -> None:
        comment = get_playground_comment(comment_id)
        if comment is None:
            abort(404)
        results: dict[str, CommentClassification] = app.extensions[
            "classification_results"
        ]
        errors: dict[str, str] = app.extensions["classification_errors"]
        try:
            results[comment_id] = classification_service().classify(comment.text)
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
        for comment in PLAYGROUND_COMMENTS:
            classify_playground_comment(comment.comment_id)
        return redirect(url_for("classification_playground"))

    return app


if __name__ == "__main__":
    create_app().run(debug=True)
