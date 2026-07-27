"""Command-line interface for local Wingman workflows."""

import argparse
import os
import sqlite3
import sys

from dotenv import load_dotenv

from wingman.db.comment_store import connect_database
from wingman.db.inbox_repository import CommentRepository


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="wingman")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("inbox", help="list active inbox comments")
    daily = subcommands.add_parser(
        "daily",
        help="sync YouTube and Instagram, then launch the web inbox",
    )
    daily.add_argument(
        "--refresh",
        action="store_true",
        help="bypass both platform freshness caches",
    )
    return parser.parse_args(argv)


def print_inbox(repository: CommentRepository) -> None:
    comments = repository.list_active_inbox_comments()
    if not comments:
        print("Wingman inbox is empty.")
        return

    for comment in comments:
        print(f"[{comment.status.upper()}]")
        print(comment.video_title)
        print(comment.author_display_name)
        print(comment.text)
        print()
        print(f"Status: {comment.status.replace('_', ' ').title()}")
        print(f"Priority: {comment.priority or '-'}")
        print(f"Category: {comment.category or '-'}")
        print("-" * 40)


def main(argv: list[str] | None = None) -> int:
    load_dotenv(dotenv_path=".env")
    args = parse_args(argv)
    if args.command == "daily":
        from wingman.daily import run_daily_workflow

        return run_daily_workflow(refresh=args.refresh)

    database_path = os.getenv("DATABASE_PATH", "comments.db").strip() or "comments.db"
    try:
        with connect_database(database_path) as connection:
            repository = CommentRepository(connection)
            if args.command == "inbox":
                print_inbox(repository)
    except sqlite3.Error as error:
        print(f"SQLite error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
