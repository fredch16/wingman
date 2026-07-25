"""Minimal Flask UI for the Wingman inbox."""

import os
import sqlite3
from typing import Any

from dotenv import load_dotenv
from flask import Flask, abort, g, redirect, render_template, request, url_for

from comment_store import connect_database
from inbox_repository import CommentRepository

INBOX_FILTERS = {"all", "new", "needs_research", "ignored", "replied"}


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    load_dotenv(dotenv_path=".env")
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE=os.getenv("DATABASE_PATH", "comments.db").strip() or "comments.db"
    )
    if test_config:
        app.config.update(test_config)

    def repository() -> CommentRepository:
        if "database" not in g:
            g.database = connect_database(app.config["DATABASE"])
        return CommentRepository(g.database)

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
        return render_template("comment_detail.html", comment=comment)

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

    return app


if __name__ == "__main__":
    create_app().run(debug=True)
