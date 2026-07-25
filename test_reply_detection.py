"""Tests for creator reply detection and one-shot persistence."""

import sqlite3
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

from comment_store import sync_comments
from fetch_comments import Comment
from reply_detection import (
    BACKLOG_CHECK_SETTING,
    backfill_reply_status,
    get_stored_creator_channel_id,
    store_creator_channel_id,
    store_setting,
)

CREATOR_CHANNEL_ID = "creator-channel"
OTHER_CHANNEL_ID = "other-channel"


def comment(total_reply_count: int) -> Comment:
    return Comment(
        comment_id="top-level-comment",
        thread_id="comment-thread",
        video_id="abcdefghijk",
        author_display_name="Viewer",
        author_channel_id=OTHER_CHANNEL_ID,
        text="Question",
        like_count=0,
        published_at="2026-07-25T10:00:00Z",
        updated_at="2026-07-25T10:00:00Z",
        total_reply_count=total_reply_count,
        can_reply=True,
        is_public=True,
    )


def reply(reply_id: str, channel_id: str) -> dict:
    return {
        "id": reply_id,
        "snippet": {
            "authorChannelId": {"value": channel_id},
            "publishedAt": "2026-07-25T11:00:00Z",
        },
    }


def youtube_with_thread(
    embedded_replies: list[dict],
    total_reply_count: int,
    full_replies: list[dict] | None = None,
) -> Mock:
    thread_request = Mock()
    thread_request.execute.return_value = {
        "items": [
            {
                "snippet": {"totalReplyCount": total_reply_count},
                "replies": {"comments": embedded_replies},
            }
        ]
    }
    comment_threads = Mock()
    comment_threads.list.return_value = thread_request

    youtube = Mock()
    youtube.commentThreads.return_value = comment_threads
    if full_replies is not None:
        replies_request = Mock()
        replies_request.execute.return_value = {"items": full_replies}
        comments = Mock()
        comments.list.return_value = replies_request
        youtube.comments.return_value = comments
    return youtube


class ReplyDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row

    def tearDown(self) -> None:
        self.connection.close()

    def stored_row(self) -> sqlite3.Row:
        row = self.connection.execute(
            """
            SELECT has_creator_reply, creator_reply_id, creator_replied_at,
                   reply_status_checked_at
            FROM comments WHERE comment_id = 'top-level-comment'
            """
        ).fetchone()
        assert row is not None
        return row

    def test_stores_authenticated_creator_channel_id(self) -> None:
        store_creator_channel_id(self.connection, CREATOR_CHANNEL_ID)

        self.assertEqual(
            get_stored_creator_channel_id(self.connection),
            CREATOR_CHANNEL_ID,
        )

    def test_no_replies_is_persisted_without_api_request(self) -> None:
        sync_comments(self.connection, [comment(total_reply_count=0)])
        youtube = Mock()

        summary = backfill_reply_status(
            youtube,
            self.connection,
            CREATOR_CHANNEL_ID,
            checked_at="2026-07-25T12:00:00Z",
        )

        self.assertEqual((summary.checked, summary.replied, summary.unreplied), (1, 0, 1))
        self.assertEqual(self.stored_row()["has_creator_reply"], 0)
        self.assertEqual(
            self.stored_row()["reply_status_checked_at"],
            "2026-07-25T12:00:00Z",
        )
        youtube.commentThreads.assert_not_called()

    def test_detects_embedded_creator_reply(self) -> None:
        sync_comments(self.connection, [comment(total_reply_count=1)])
        youtube = youtube_with_thread(
            [reply("creator-reply", CREATOR_CHANNEL_ID)], 1
        )

        summary = backfill_reply_status(
            youtube, self.connection, CREATOR_CHANNEL_ID
        )

        row = self.stored_row()
        self.assertEqual((summary.replied, summary.unreplied), (1, 0))
        self.assertEqual(row["has_creator_reply"], 1)
        self.assertEqual(row["creator_reply_id"], "creator-reply")
        self.assertEqual(row["creator_replied_at"], "2026-07-25T11:00:00Z")

    def test_replies_from_other_users_are_unreplied(self) -> None:
        sync_comments(self.connection, [comment(total_reply_count=1)])
        youtube = youtube_with_thread(
            [reply("other-reply", OTHER_CHANNEL_ID)], 1
        )

        summary = backfill_reply_status(
            youtube, self.connection, CREATOR_CHANNEL_ID
        )

        self.assertEqual((summary.replied, summary.unreplied), (0, 1))
        self.assertEqual(self.stored_row()["has_creator_reply"], 0)

    def test_incomplete_embedded_replies_fetch_full_reply_list(self) -> None:
        sync_comments(self.connection, [comment(total_reply_count=2)])
        youtube = youtube_with_thread(
            [reply("other-reply", OTHER_CHANNEL_ID)],
            2,
            full_replies=[
                reply("other-reply", OTHER_CHANNEL_ID),
                reply("creator-reply", CREATOR_CHANNEL_ID),
            ],
        )

        summary = backfill_reply_status(
            youtube, self.connection, CREATOR_CHANNEL_ID
        )

        self.assertEqual(summary.replied, 1)
        self.assertEqual(self.stored_row()["creator_reply_id"], "creator-reply")
        youtube.comments.return_value.list.assert_called_once()

    def test_repeated_runs_do_not_recheck_confirmed_comments(self) -> None:
        sync_comments(self.connection, [comment(total_reply_count=1)])
        youtube = youtube_with_thread(
            [reply("creator-reply", CREATOR_CHANNEL_ID)], 1
        )

        first = backfill_reply_status(
            youtube, self.connection, CREATOR_CHANNEL_ID
        )
        second = backfill_reply_status(
            youtube, self.connection, CREATOR_CHANNEL_ID
        )

        self.assertEqual(first.checked, 1)
        self.assertEqual(second.checked, 0)
        self.assertTrue(second.skipped)
        youtube.commentThreads.return_value.list.assert_called_once()

    def test_recent_backlog_check_skips_pending_comments(self) -> None:
        sync_comments(self.connection, [comment(total_reply_count=0)])
        store_setting(
            self.connection,
            BACKLOG_CHECK_SETTING,
            "2026-07-25T12:00:00Z",
        )

        summary = backfill_reply_status(
            Mock(),
            self.connection,
            CREATOR_CHANNEL_ID,
            now=datetime(2026, 7, 25, 12, 15, tzinfo=timezone.utc),
        )

        self.assertTrue(summary.skipped)
        self.assertEqual(summary.checked, 0)
        self.assertIsNone(self.stored_row()["reply_status_checked_at"])

    def test_refresh_overrides_recent_backlog_check(self) -> None:
        sync_comments(self.connection, [comment(total_reply_count=0)])
        store_setting(
            self.connection,
            BACKLOG_CHECK_SETTING,
            "2026-07-25T12:00:00Z",
        )

        summary = backfill_reply_status(
            Mock(),
            self.connection,
            CREATOR_CHANNEL_ID,
            refresh=True,
            checked_at="2026-07-25T12:15:00Z",
            now=datetime(2026, 7, 25, 12, 15, tzinfo=timezone.utc),
        )

        self.assertFalse(summary.skipped)
        self.assertEqual(summary.checked, 1)


if __name__ == "__main__":
    unittest.main()
