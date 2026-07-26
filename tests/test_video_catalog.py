"""Tests for channel video discovery and enabled-video selection."""

import sqlite3
import unittest
from unittest.mock import ANY, Mock, patch

from wingman.youtube.sync import Comment, FetchResult, sync_videos
from wingman.db.video_catalog import (
    Video,
    append_video_response_guidance,
    create_videos_table,
    enabled_video_ids,
    fetch_all_upload_videos,
    list_stored_videos,
    set_video_enabled,
    store_discovered_videos,
    update_video_summary,
)


def playlist_item(video_id: str, title: str) -> dict:
    return {
        "snippet": {
            "resourceId": {"videoId": video_id},
            "title": title,
            "publishedAt": "2026-07-25T10:00:00Z",
            "thumbnails": {"high": {"url": f"https://img/{video_id}.jpg"}},
        },
        "contentDetails": {"videoPublishedAt": "2026-07-25T09:00:00Z"},
    }


def discovery_client(*pages: dict) -> Mock:
    channel_request = Mock()
    channel_request.execute.return_value = {
        "items": [
            {
                "contentDetails": {
                    "relatedPlaylists": {"uploads": "uploads-playlist"}
                }
            }
        ]
    }
    channels = Mock()
    channels.list.return_value = channel_request

    page_requests = []
    for page in pages:
        request = Mock()
        request.execute.return_value = page
        page_requests.append(request)
    playlist_items = Mock()
    playlist_items.list.side_effect = page_requests

    youtube = Mock()
    youtube.channels.return_value = channels
    youtube.playlistItems.return_value = playlist_items
    return youtube


def stored_video(video_id: str, title: str) -> Video:
    return Video(
        video_id=video_id,
        title=title,
        published_at="2026-07-25T09:00:00Z",
        thumbnail_url=f"https://img/{video_id}.jpg",
    )


def fetched_comment(video_id: str) -> Comment:
    return Comment(
        comment_id=f"comment-{video_id}",
        thread_id=f"thread-{video_id}",
        video_id=video_id,
        author_display_name="Author",
        author_channel_id=None,
        text="Comment",
        like_count=0,
        published_at="2026-07-25T10:00:00Z",
        updated_at="2026-07-25T10:00:00Z",
        total_reply_count=0,
        can_reply=True,
        is_public=True,
    )


class VideoCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row

    def tearDown(self) -> None:
        self.connection.close()

    def test_migrates_existing_videos_with_comment_check_timestamp(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE videos (
                video_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                published_at TEXT NOT NULL,
                thumbnail_url TEXT NOT NULL,
                is_enabled INTEGER NOT NULL DEFAULT 1,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            )
            """
        )

        create_videos_table(self.connection)

        columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(videos)")
        }
        self.assertIn("comments_last_checked_at", columns)
        self.assertIn("summary", columns)

    def test_uploads_playlist_pagination(self) -> None:
        youtube = discovery_client(
            {
                "items": [playlist_item("abcdefghijk", "First")],
                "nextPageToken": "page-2",
            },
            {"items": [playlist_item("lmnopqrstuv", "Second")]},
        )

        result = fetch_all_upload_videos(youtube)

        self.assertEqual(result.pages_fetched, 2)
        self.assertEqual(
            [video.video_id for video in result.videos],
            ["abcdefghijk", "lmnopqrstuv"],
        )
        calls = youtube.playlistItems.return_value.list.call_args_list
        self.assertIsNone(calls[0].kwargs["pageToken"])
        self.assertEqual(calls[1].kwargs["pageToken"], "page-2")

    def test_discovery_preserves_enabled_state(self) -> None:
        video = stored_video("abcdefghijk", "Original title")
        store_discovered_videos(
            self.connection, [video], "2026-07-25T10:00:00Z"
        )
        set_video_enabled(self.connection, video.video_id, False)

        summary = store_discovered_videos(
            self.connection,
            [stored_video("abcdefghijk", "Updated title")],
            "2026-07-25T11:00:00Z",
        )

        stored = list_stored_videos(self.connection)[0]
        self.assertEqual(summary.updated, 1)
        self.assertEqual(stored.title, "Updated title")
        self.assertFalse(stored.is_enabled)
        self.assertEqual(stored.first_seen_at, "2026-07-25T10:00:00Z")
        self.assertEqual(stored.last_seen_at, "2026-07-25T11:00:00Z")

    def test_manual_summary_survives_later_discovery(self) -> None:
        video = stored_video("abcdefghijk", "Original title")
        store_discovered_videos(self.connection, [video])
        self.assertTrue(
            update_video_summary(
                self.connection,
                video.video_id,
                "Manual context for reply generation.",
            )
        )

        store_discovered_videos(
            self.connection,
            [stored_video("abcdefghijk", "Updated title")],
        )

        stored = list_stored_videos(self.connection)[0]
        self.assertEqual(stored.title, "Updated title")
        self.assertEqual(
            stored.summary, "Manual context for reply generation."
        )

    def test_appends_response_guidance_without_replacing_video_summary(self) -> None:
        video = stored_video("abcdefghijk", "Button demo")
        store_discovered_videos(self.connection, [video])
        update_video_summary(
            self.connection,
            video.video_id,
            "The button changes the controller setpoint.",
        )

        self.assertTrue(
            append_video_response_guidance(
                self.connection,
                video.video_id,
                "When asked about the button, explain the setpoint change.",
            )
        )
        self.assertTrue(
            append_video_response_guidance(
                self.connection,
                video.video_id,
                "When asked about the button, explain the setpoint change.",
            )
        )

        summary = list_stored_videos(self.connection)[0].summary
        self.assertTrue(
            summary.startswith("The button changes the controller setpoint.")
        )
        self.assertEqual(summary.count("## Response Guidance"), 1)
        self.assertEqual(summary.count("When asked about the button"), 1)

    @patch("wingman.youtube.sync.fetch_all_comments_for_video")
    def test_only_enabled_videos_are_synced(self, fetch: Mock) -> None:
        store_discovered_videos(
            self.connection,
            [
                stored_video("abcdefghijk", "Enabled"),
                stored_video("lmnopqrstuv", "Disabled"),
            ],
        )
        set_video_enabled(self.connection, "lmnopqrstuv", False)
        fetch.return_value = FetchResult(
            [fetched_comment("abcdefghijk")], pages_fetched=1
        )

        results = sync_videos(
            Mock(), enabled_video_ids(self.connection), self.connection
        )

        fetch.assert_called_once_with(ANY, "abcdefghijk")
        self.assertEqual([result.video_id for result in results], ["abcdefghijk"])

    @patch("wingman.youtube.sync.fetch_all_comments_for_video")
    def test_recent_comment_sync_skips_unless_refreshed(self, fetch: Mock) -> None:
        store_discovered_videos(
            self.connection,
            [stored_video("abcdefghijk", "Video")],
        )
        fetch.return_value = FetchResult(
            [fetched_comment("abcdefghijk")], pages_fetched=1
        )

        first = sync_videos(Mock(), ["abcdefghijk"], self.connection)
        second = sync_videos(Mock(), ["abcdefghijk"], self.connection)
        forced = sync_videos(
            Mock(), ["abcdefghijk"], self.connection, refresh=True
        )

        self.assertIsNone(first[0].skipped_reason)
        self.assertIn("last hour", second[0].skipped_reason or "")
        self.assertIsNone(forced[0].skipped_reason)
        self.assertEqual(fetch.call_count, 2)


if __name__ == "__main__":
    unittest.main()
