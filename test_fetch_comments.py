"""Focused tests for YouTube comment pagination."""

import sqlite3
import unittest
from typing import Any
from unittest.mock import Mock, patch

from comment_store import create_comments_table
from fetch_comments import (
    Comment,
    FetchResult,
    PageFetchError,
    PaginationError,
    fetch_all_comments_for_video,
    sync_videos,
)


def thread(comment_id: str) -> dict[str, Any]:
    return {
        "id": f"thread-{comment_id}",
        "snippet": {
            "videoId": "abcdefghijk",
            "topLevelComment": {
                "id": comment_id,
                "snippet": {
                    "authorDisplayName": "Author",
                    "authorChannelId": {"value": "channel-id"},
                    "textDisplay": f"Text for {comment_id}",
                    "likeCount": 2,
                    "publishedAt": "2026-07-25T10:00:00Z",
                    "updatedAt": "2026-07-25T11:00:00Z",
                },
            },
            "totalReplyCount": 3,
            "canReply": True,
            "isPublic": True,
        },
    }


def youtube_client(*responses: object) -> Mock:
    requests = []
    for response in responses:
        request = Mock()
        if isinstance(response, Exception):
            request.execute.side_effect = response
        else:
            request.execute.return_value = response
        requests.append(request)

    comment_threads = Mock()
    comment_threads.list.side_effect = requests
    youtube = Mock()
    youtube.commentThreads.return_value = comment_threads
    return youtube


def comment(comment_id: str, video_id: str) -> Comment:
    return Comment(
        comment_id=comment_id,
        thread_id=f"thread-{comment_id}",
        video_id=video_id,
        author_display_name="Author",
        author_channel_id="channel-id",
        text=f"Text for {comment_id}",
        like_count=2,
        published_at="2026-07-25T10:00:00Z",
        updated_at="2026-07-25T11:00:00Z",
        total_reply_count=3,
        can_reply=True,
        is_public=True,
    )


class PaginationTests(unittest.TestCase):
    def test_one_page_without_next_token(self) -> None:
        youtube = youtube_client({"items": [thread("one")]})

        result = fetch_all_comments_for_video(youtube, "abcdefghijk")

        self.assertEqual(result.pages_fetched, 1)
        self.assertEqual([comment.comment_id for comment in result.comments], ["one"])

    def test_multiple_pages(self) -> None:
        youtube = youtube_client(
            {"items": [thread("one")], "nextPageToken": "page-2"},
            {"items": [thread("two")]},
        )

        result = fetch_all_comments_for_video(youtube, "abcdefghijk")

        self.assertEqual(result.pages_fetched, 2)
        self.assertEqual(
            [comment.comment_id for comment in result.comments], ["one", "two"]
        )
        second_call = youtube.commentThreads.return_value.list.call_args_list[1]
        self.assertEqual(second_call.kwargs["pageToken"], "page-2")

    def test_duplicate_comment_ids_across_pages(self) -> None:
        youtube = youtube_client(
            {"items": [thread("one")], "nextPageToken": "page-2"},
            {"items": [thread("one"), thread("two")]},
        )

        result = fetch_all_comments_for_video(youtube, "abcdefghijk")

        self.assertEqual(
            [comment.comment_id for comment in result.comments], ["one", "two"]
        )

    def test_empty_result(self) -> None:
        result = fetch_all_comments_for_video(
            youtube_client({"items": []}), "abcdefghijk"
        )

        self.assertEqual(result.pages_fetched, 1)
        self.assertEqual(result.comments, [])

    def test_repeated_next_page_token(self) -> None:
        youtube = youtube_client(
            {"items": [thread("one")], "nextPageToken": "repeated"},
            {"items": [thread("two")], "nextPageToken": "repeated"},
        )

        with self.assertRaisesRegex(PaginationError, "Repeated nextPageToken"):
            fetch_all_comments_for_video(youtube, "abcdefghijk")

    def test_api_failure_on_later_page(self) -> None:
        youtube = youtube_client(
            {"items": [thread("one")], "nextPageToken": "page-2"},
            RuntimeError("API unavailable"),
        )

        with self.assertRaises(PageFetchError) as raised:
            fetch_all_comments_for_video(youtube, "abcdefghijk")

        self.assertEqual(raised.exception.page_number, 2)
        self.assertEqual(str(raised.exception.cause), "API unavailable")


class MultipleVideoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        create_comments_table(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    @patch("fetch_comments.fetch_all_comments_for_video")
    def test_multiple_videos_are_synced_separately(self, fetch: Mock) -> None:
        fetch.side_effect = [
            FetchResult([comment("comment-1", "video00001")], 1),
            FetchResult([comment("comment-2", "video00002")], 2),
        ]

        results = sync_videos(
            Mock(), ["video00001", "video00002"], self.connection
        )

        rows = self.connection.execute(
            "SELECT comment_id, video_id FROM comments ORDER BY comment_id"
        ).fetchall()
        self.assertEqual([result.error for result in results], [None, None])
        self.assertEqual(
            [(row["comment_id"], row["video_id"]) for row in rows],
            [("comment-1", "video00001"), ("comment-2", "video00002")],
        )

    @patch("fetch_comments.fetch_all_comments_for_video")
    def test_failure_on_one_video_does_not_stop_the_next(self, fetch: Mock) -> None:
        fetch.side_effect = [
            PageFetchError(1, RuntimeError("Comments are disabled")),
            FetchResult([comment("comment-2", "video00002")], 1),
        ]

        results = sync_videos(
            Mock(), ["video00001", "video00002"], self.connection
        )

        stored = self.connection.execute(
            "SELECT comment_id, video_id FROM comments"
        ).fetchone()
        self.assertIn("Comments are disabled", results[0].error or "")
        self.assertIsNone(results[1].error)
        self.assertEqual((stored["comment_id"], stored["video_id"]), (
            "comment-2",
            "video00002",
        ))


if __name__ == "__main__":
    unittest.main()
