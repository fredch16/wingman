"""No-network tests for count-gated YouTube comment checks."""

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from wingman.db.comment_store import SyncSummary
from wingman.db.video_catalog import Video, store_discovered_videos
from wingman.youtube.sync import FetchResult, VideoSyncResult
from wingman.youtube.watch import fetch_video_comment_counts, watch_comments
from wingman.youtube.automation import AutoReplySummary


class YouTubeWatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        store_discovered_videos(self.connection, [
            Video("youtube-one", "YouTube video", "2026-09-20T00:00:00Z", ""),
            Video("instagram-one", "Instagram Reel", "2026-09-20T00:00:00Z", "", platform="instagram"),
        ])
        self.youtube = MagicMock()
        self.youtube.videos.return_value.list.return_value.execute.return_value = {
            "items": [{"id": "youtube-one", "statistics": {"commentCount": "12"}}]
        }
        self.now = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.connection.close()

    def test_first_check_scans_then_unchanged_count_skips(self) -> None:
        synced = VideoSyncResult(
            "youtube-one", FetchResult([], 1), SyncSummary(12, 2, 0, 10)
        )
        with patch("wingman.youtube.watch.sync_videos", return_value=[synced]) as sync:
            first = watch_comments(self.youtube, self.connection, now=self.now)
            second = watch_comments(self.youtube, self.connection, now=self.now + timedelta(minutes=10))
        self.assertEqual((first.videos_scanned, first.new_comments), (1, 2))
        self.assertEqual((second.videos_scanned, second.videos_skipped), (0, 1))
        self.assertEqual(sync.call_count, 1)
        self.assertEqual(self.youtube.videos.return_value.list.call_args.kwargs["id"], "youtube-one")

    def test_count_change_and_daily_reconciliation_both_scan(self) -> None:
        synced = VideoSyncResult("youtube-one", FetchResult([], 1), SyncSummary(13, 1, 0, 12))
        with patch("wingman.youtube.watch.sync_videos", return_value=[synced]) as sync:
            watch_comments(self.youtube, self.connection, now=self.now)
            self.youtube.videos.return_value.list.return_value.execute.return_value = {
                "items": [{"id": "youtube-one", "statistics": {"commentCount": "13"}}]
            }
            changed = watch_comments(self.youtube, self.connection, now=self.now + timedelta(minutes=10))
            overdue = watch_comments(self.youtube, self.connection, now=self.now + timedelta(days=2))
        self.assertEqual((changed.videos_scanned, overdue.videos_scanned), (1, 1))
        self.assertEqual(sync.call_count, 3)

    def test_failed_scan_does_not_advance_checkpoint(self) -> None:
        failed = VideoSyncResult("youtube-one", error="Page 2 failed")
        with patch("wingman.youtube.watch.sync_videos", return_value=[failed]) as sync:
            first = watch_comments(self.youtube, self.connection, now=self.now)
            second = watch_comments(self.youtube, self.connection, now=self.now + timedelta(minutes=10))
        self.assertEqual((first.videos_failed, second.videos_failed), (1, 1))
        self.assertEqual(sync.call_count, 2)
        self.assertIsNone(self.connection.execute("SELECT * FROM youtube_comment_watch").fetchone())

    def test_disabled_comments_do_not_retry_every_check(self) -> None:
        disabled = VideoSyncResult("youtube-one", error="Page 1 failed: Comments are disabled")
        with patch("wingman.youtube.watch.sync_videos", return_value=[disabled]) as sync:
            first = watch_comments(self.youtube, self.connection, now=self.now)
            second = watch_comments(self.youtube, self.connection, now=self.now + timedelta(minutes=10))
        self.assertEqual((first.videos_failed, second.videos_skipped), (0, 1))
        self.assertEqual(sync.call_count, 1)

    def test_count_fetch_batches_fifty_ids_per_request(self) -> None:
        ids = [f"video-{number}" for number in range(51)]
        self.youtube.videos.return_value.list.return_value.execute.side_effect = [
            {"items": [{"id": video_id, "statistics": {"commentCount": "0"}} for video_id in ids[:50]]},
            {"items": [{"id": ids[50], "statistics": {"commentCount": "1"}}]},
        ]
        counts, requests = fetch_video_comment_counts(self.youtube, ids)
        self.assertEqual((len(counts), requests), (51, 2))
        self.assertEqual(self.youtube.videos.return_value.list.call_count, 2)

    def test_automation_runs_after_successful_scan_not_skipped_check(self) -> None:
        synced = VideoSyncResult("youtube-one", FetchResult([], 1), SyncSummary(0, 0, 0, 0))
        with patch("wingman.youtube.watch.sync_videos", return_value=[synced]), patch(
            "wingman.youtube.watch.run_youtube_auto_replies",
            return_value=AutoReplySummary(matched=1, sent=1),
        ) as automate:
            first = watch_comments(self.youtube, self.connection, now=self.now)
            watch_comments(self.youtube, self.connection, now=self.now + timedelta(minutes=10))
        self.assertEqual(first.auto_replies_sent, 1)
        self.assertEqual(automate.call_count, 1)


if __name__ == "__main__":
    unittest.main()
