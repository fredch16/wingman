"""Analytics count recorded automations, not all manual replies."""

import sqlite3
import unittest

from wingman.db.automation_analytics import get_automation_analytics
from wingman.db.automation_repository import AutomationRepository
from wingman.db.comment_store import sync_comments
from wingman.db.video_catalog import Video, store_discovered_videos
from wingman.instagram.sync import Comment as InstagramComment
from wingman.youtube.sync import Comment as YouTubeComment


class AutomationAnalyticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        store_discovered_videos(self.connection, [
            Video("youtube-video", "PCB Short", "2026-09-20T00:00:00Z", ""),
            Video("instagram-video", "PCB Reel", "2026-09-20T00:00:00Z", "", platform="instagram"),
        ])
        sync_comments(self.connection, [
            self.comment(YouTubeComment, "yt-1", "youtube-video"),
            self.comment(YouTubeComment, "manual", "youtube-video"),
            self.comment(InstagramComment, "ig-1", "instagram-video"),
            self.comment(InstagramComment, "ig-2", "instagram-video"),
        ])
        self.repo = AutomationRepository(self.connection)
        self.youtube_rule = self.repo.create(
            "youtube-video", "PCB", "Guide", mode="youtube_reply"
        )
        self.instagram_rule = self.repo.create(
            "instagram-video", "PCB", "Sent", mode="instagram_dm",
            initial_dm="Want it?", followup_dm="Here it is",
        )

    def tearDown(self) -> None:
        self.connection.close()

    @staticmethod
    def comment(kind, comment_id, video_id):
        return kind(
            comment_id=comment_id, thread_id=comment_id, video_id=video_id,
            author_display_name="Viewer", author_channel_id="viewer", text="PCB",
            like_count=0, published_at="2026-09-20T00:00:00Z",
            updated_at="2026-09-20T00:00:00Z", total_reply_count=0,
            can_reply=True, is_public=True,
        )

    def test_totals_deduplicate_and_include_deleted_rules(self) -> None:
        self.assertTrue(self.repo.claim_delivery("yt-1", self.youtube_rule, "viewer"))
        self.repo.update_delivery("yt-1", "completed", public_reply_id="yt-reply")
        self.assertFalse(self.repo.claim_delivery("yt-1", self.youtube_rule, "viewer"))
        self.assertTrue(self.repo.claim_delivery("ig-1", self.instagram_rule, "viewer"))
        self.repo.update_delivery(
            "ig-1", "completed", public_reply_id="ig-reply",
            followup_message_id="dm-1",
        )
        self.assertTrue(self.repo.claim_delivery("ig-2", self.instagram_rule, "viewer"))
        self.repo.update_delivery("ig-2", "private_failed", error="Meta error")
        self.repo.delete(self.instagram_rule)

        analytics = get_automation_analytics(self.connection)

        self.assertEqual(
            (analytics.total.triggered, analytics.total.public_replies,
             analytics.total.followup_dms, analytics.total.failed),
            (3, 2, 1, 1),
        )
        self.assertEqual(
            {item.platform: item.metrics.triggered for item in analytics.platforms},
            {"instagram": 2, "youtube": 1},
        )
        self.assertEqual(
            {item.video_id: item.metrics.triggered for item in analytics.videos},
            {"instagram-video": 2, "youtube-video": 1},
        )
        self.assertEqual(analytics.videos[0].title, "PCB Reel")
        self.assertEqual(len(analytics.recent), 3)

    def test_empty_history_has_zero_metrics(self) -> None:
        analytics = get_automation_analytics(self.connection)
        self.assertEqual(analytics.total.triggered, 0)
        self.assertEqual(analytics.platforms, ())
        self.assertEqual(analytics.videos, ())


if __name__ == "__main__":
    unittest.main()
