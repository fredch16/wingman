"""Low-cost, count-gated YouTube comment synchronization prototype."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from dotenv import load_dotenv
from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.errors import HttpError

from wingman.db.comment_store import connect_database
from wingman.db.video_catalog import create_videos_table
from wingman.youtube.automation import run_youtube_auto_replies
from wingman.youtube.sync import api_error_message, get_authenticated_youtube_client, sync_videos


@dataclass(frozen=True)
class WatchSummary:
    videos_checked: int
    videos_scanned: int
    videos_skipped: int
    videos_failed: int
    new_comments: int
    count_requests: int
    auto_replies_sent: int = 0
    auto_replies_failed: int = 0


def create_watch_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS youtube_comment_watch (
            video_id TEXT PRIMARY KEY,
            comment_count INTEGER,
            count_checked_at TEXT NOT NULL,
            full_scan_at TEXT NOT NULL
        )"""
    )


def enabled_youtube_video_ids(connection: sqlite3.Connection) -> list[str]:
    create_videos_table(connection)
    return [row[0] for row in connection.execute(
        "SELECT video_id FROM videos WHERE platform = 'youtube' AND is_enabled = 1 ORDER BY published_at DESC"
    )]


def fetch_video_comment_counts(
    youtube: Any, video_ids: list[str]
) -> tuple[dict[str, int | None], int]:
    """Read up to 50 video counts per API request; missing counts force a scan."""
    counts: dict[str, int | None] = {}
    requests = 0
    for offset in range(0, len(video_ids), 50):
        batch = video_ids[offset : offset + 50]
        response = youtube.videos().list(
            part="statistics", id=",".join(batch)
        ).execute()
        requests += 1
        for item in response.get("items", []):
            raw_count = item.get("statistics", {}).get("commentCount")
            counts[str(item["id"])] = int(raw_count) if raw_count is not None else None
    return counts, requests


def save_full_scan_checkpoint(
    connection: sqlite3.Connection, video_id: str, count: int | None, checked_at: str
) -> None:
    with connection:
        connection.execute(
            """INSERT INTO youtube_comment_watch
               (video_id, comment_count, count_checked_at, full_scan_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(video_id) DO UPDATE SET
                 comment_count = excluded.comment_count,
                 count_checked_at = excluded.count_checked_at,
                 full_scan_at = excluded.full_scan_at""",
            (video_id, count, checked_at, checked_at),
        )


def watch_comments(
    youtube: Any,
    connection: sqlite3.Connection,
    *,
    now: datetime | None = None,
    reconcile_after: timedelta = timedelta(days=1),
    refresh: bool = False,
) -> WatchSummary:
    """Scan only changed videos; force a full reconciliation periodically."""
    create_watch_table(connection)
    video_ids = enabled_youtube_video_ids(connection)
    if not video_ids:
        return WatchSummary(0, 0, 0, 0, 0, 0)
    current_time = now or datetime.now(timezone.utc)
    counts, requests = fetch_video_comment_counts(youtube, video_ids)
    scanned = skipped = failed = new_comments = auto_sent = auto_failed = 0
    print(f"Checked comment counts for {len(video_ids)} YouTube videos in {requests} API request(s).")
    for video_id in video_ids:
        if video_id not in counts:
            failed += 1
            print(f"{video_id}: video statistics unavailable; not marking it checked.")
            continue
        count = counts[video_id]
        previous = connection.execute(
            "SELECT comment_count, full_scan_at FROM youtube_comment_watch WHERE video_id = ?",
            (video_id,),
        ).fetchone()
        overdue = previous is None or (
            current_time - datetime.fromisoformat(
                previous["full_scan_at"].replace("Z", "+00:00")
            ) >= reconcile_after
        )
        changed = previous is None or previous["comment_count"] != count
        if not (refresh or changed or overdue):
            skipped += 1
            print(f"{video_id}: count unchanged ({count}); skipping comment fetch.")
            with connection:
                connection.execute(
                    "UPDATE youtube_comment_watch SET count_checked_at = ? WHERE video_id = ?",
                    (current_time.isoformat(), video_id),
                )
            continue
        reason = "manual refresh" if refresh else "count changed" if changed else "reconciliation due"
        print(f"{video_id}: {reason}; fetching comments...")
        result = sync_videos(youtube, [video_id], connection, refresh=True)[0]
        if result.error:
            if "Comments are disabled" in result.error:
                print(f"{video_id}: comments disabled; checking again at reconciliation.")
                save_full_scan_checkpoint(connection, video_id, count, current_time.isoformat())
                skipped += 1
                continue
            failed += 1
            print(f"{video_id}: failed: {result.error}")
            continue
        if result.fetch_result is None or result.sync_summary is None:
            failed += 1
            print(f"{video_id}: did not complete; count checkpoint unchanged.")
            continue
        scanned += 1
        new_comments += result.sync_summary.newly_inserted
        print(
            f"{video_id}: {result.sync_summary.newly_inserted} new comment(s), "
            f"{result.fetch_result.pages_fetched} page(s)."
        )
        auto = run_youtube_auto_replies(youtube, connection, result.fetch_result.comments)
        auto_sent += auto.sent
        auto_failed += auto.failed
        if auto.matched:
            print(
                f"{video_id}: {auto.matched} rule match(es), {auto.sent} auto-replies sent, "
                f"{auto.skipped} skipped, {auto.failed} failed."
            )
        save_full_scan_checkpoint(connection, video_id, count, current_time.isoformat())
    return WatchSummary(
        len(video_ids), scanned, skipped, failed, new_comments, requests,
        auto_sent, auto_failed,
    )


def main(argv: list[str] | None = None) -> int:
    load_dotenv(".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=os.getenv("DATABASE_PATH", "").strip() or "comments.db")
    parser.add_argument("--refresh", action="store_true", help="scan every enabled YouTube video now")
    parser.add_argument("--reconcile-hours", type=int, default=24)
    args = parser.parse_args(argv)
    if args.reconcile_hours < 1:
        parser.error("--reconcile-hours must be at least 1")
    try:
        youtube = get_authenticated_youtube_client()
        connection = connect_database(args.database)
        try:
            summary = watch_comments(
                youtube, connection,
                reconcile_after=timedelta(hours=args.reconcile_hours),
                refresh=args.refresh,
            )
        finally:
            connection.close()
    except (HttpError, RefreshError, TransportError, sqlite3.Error, ValueError) as error:
        message = api_error_message(error) if isinstance(error, HttpError) else str(error)
        print(f"YouTube watch failed: {message}", file=sys.stderr)
        return 1
    print(
        f"Done: {summary.videos_checked} checked, {summary.videos_scanned} scanned, "
        f"{summary.videos_skipped} unchanged, {summary.videos_failed} failed, "
        f"{summary.new_comments} new comments, {summary.auto_replies_sent} auto-replies sent, "
        f"{summary.auto_replies_failed} auto-replies failed."
    )
    return 1 if summary.videos_failed or summary.auto_replies_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
