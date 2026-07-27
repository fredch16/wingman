"""Command-line interface for local Wingman workflows."""

import argparse
import os
import sqlite3
import sys

from dotenv import load_dotenv

from wingman.db.comment_store import connect_database
from wingman.db.inbox_repository import CommentRepository
from wingman.db.state_snapshot import (
    DEFAULT_SNAPSHOT_PATH,
    StateSnapshotError,
    inspect_state_snapshot,
    restore_state_snapshot,
    save_state_snapshot,
)


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
    state = subcommands.add_parser(
        "state",
        help="save or restore a Git-trackable database snapshot",
    )
    state_actions = state.add_subparsers(dest="state_action", required=True)
    for action, help_text in (
        ("save", "create or update the compressed state snapshot"),
        ("restore", "restore the database from the tracked state snapshot"),
        ("status", "validate and describe the tracked state snapshot"),
    ):
        command = state_actions.add_parser(action, help=help_text)
        command.add_argument(
            "--snapshot",
            default=str(DEFAULT_SNAPSHOT_PATH),
            help=f"snapshot path (default: {DEFAULT_SNAPSHOT_PATH})",
        )
        if action != "status":
            command.add_argument(
                "--database",
                default=None,
                help="database path (default: DATABASE_PATH from .env)",
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
    if args.command == "state":
        try:
            if args.state_action == "save":
                info = save_state_snapshot(
                    args.database or database_path,
                    args.snapshot,
                )
                print(f"Saved Wingman state: {info.path}")
                print(f"Compressed size: {_format_bytes(info.compressed_bytes)}")
                print(f"Created: {info.created_at}")
                print(
                    "Privacy warning: this snapshot contains comments, usernames, "
                    "drafts, and classifications."
                )
            elif args.state_action == "restore":
                result = restore_state_snapshot(
                    args.database or database_path,
                    args.snapshot,
                )
                print(f"Restored Wingman state from: {result.snapshot.path}")
                if result.recovery_path:
                    print(
                        f"Previous local database backed up to: "
                        f"{result.recovery_path}"
                    )
            else:
                info = inspect_state_snapshot(args.snapshot)
                print(f"Wingman state is valid: {info.path}")
                print(f"Created: {info.created_at}")
                print(f"Compressed size: {_format_bytes(info.compressed_bytes)}")
                print(f"SHA-256: {info.sha256}")
        except (OSError, sqlite3.Error, StateSnapshotError) as error:
            print(f"State error: {error}", file=sys.stderr)
            return 1
        return 0

    try:
        with connect_database(database_path) as connection:
            repository = CommentRepository(connection)
            if args.command == "inbox":
                print_inbox(repository)
    except sqlite3.Error as error:
        print(f"SQLite error: {error}", file=sys.stderr)
        return 1
    return 0


def _format_bytes(byte_count: int) -> str:
    if byte_count < 1024:
        return f"{byte_count} B"
    if byte_count < 1024 * 1024:
        return f"{byte_count / 1024:.1f} KiB"
    return f"{byte_count / (1024 * 1024):.1f} MiB"


if __name__ == "__main__":
    raise SystemExit(main())
