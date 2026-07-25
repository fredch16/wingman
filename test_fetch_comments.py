"""Focused tests for YouTube comment pagination."""

import unittest
from typing import Any
from unittest.mock import Mock

from fetch_comments import PageFetchError, PaginationError, fetch_all_comments_for_video


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


if __name__ == "__main__":
    unittest.main()
