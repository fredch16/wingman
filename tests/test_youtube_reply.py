"""Tests for posting a top-level comment reply."""

import unittest
from unittest.mock import Mock

from wingman.youtube.reply import PostedReply, post_comment_reply


class YouTubeReplyTests(unittest.TestCase):
    def test_posts_reply_with_parent_comment_id(self) -> None:
        execute = Mock(
            return_value={
                "id": "youtube-reply-1",
                "snippet": {
                    "textOriginal": "A useful reply",
                    "publishedAt": "2026-07-25T14:00:00Z",
                },
            }
        )
        insert = Mock(return_value=Mock(execute=execute))
        youtube = Mock()
        youtube.comments.return_value.insert = insert

        result = post_comment_reply(
            youtube, "top-level-comment-1", "  A useful reply  "
        )

        insert.assert_called_once_with(
            part="snippet",
            body={
                "snippet": {
                    "parentId": "top-level-comment-1",
                    "textOriginal": "A useful reply",
                }
            },
        )
        self.assertEqual(
            result,
            PostedReply(
                reply_id="youtube-reply-1",
                text="A useful reply",
                published_at="2026-07-25T14:00:00Z",
            ),
        )

    def test_rejects_empty_reply_without_api_call(self) -> None:
        youtube = Mock()

        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            post_comment_reply(youtube, "comment-1", "   ")

        youtube.comments.assert_not_called()


if __name__ == "__main__":
    unittest.main()
