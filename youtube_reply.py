"""Post creator replies to YouTube comments."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PostedReply:
    reply_id: str
    text: str
    published_at: str | None


def post_comment_reply(
    youtube: Any, parent_comment_id: str, text: str
) -> PostedReply:
    """Post one reply and return the fields needed by the inbox."""
    reply_text = text.strip()
    if not reply_text:
        raise ValueError("Reply text cannot be empty.")

    response = (
        youtube.comments()
        .insert(
            part="snippet",
            body={
                "snippet": {
                    "parentId": parent_comment_id,
                    "textOriginal": reply_text,
                }
            },
        )
        .execute()
    )
    snippet = response.get("snippet", {})
    return PostedReply(
        reply_id=response["id"],
        text=snippet.get("textOriginal", reply_text),
        published_at=snippet.get("publishedAt"),
    )
