import json
import time
from http.client import RemoteDisconnected
from typing import Iterator, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class InstagramApiError(RuntimeError):
    """Raised when the Instagram Graph API cannot complete an operation."""


class InstagramClient:
    def __init__(self, access_token: str, api_version: str = "v25.0"):
        if not access_token:
            raise InstagramApiError("INSTAGRAM_ACCESS_TOKEN is missing from .env.")
        self.access_token = access_token
        self.base_url = f"https://graph.instagram.com/{api_version.strip('/')}"

    def _send(self, request: Request) -> dict:
        last_error = None
        for attempt in range(3):
            try:
                with urlopen(request, timeout=30) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                details = exc.read().decode("utf-8", errors="replace")
                raise InstagramApiError(
                    f"Instagram API error {exc.code}: {details}"
                ) from exc
            except (RemoteDisconnected, TimeoutError, URLError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2**attempt)
                    continue
                break

        raise InstagramApiError(f"Could not reach Instagram API: {last_error}")

    def get(self, path_or_url: str, params: Optional[dict] = None) -> dict:
        params = dict(params or {})

        if path_or_url.startswith("https://"):
            url = path_or_url
        else:
            path = path_or_url.strip("/")
            url = f"{self.base_url}/{path}?{urlencode(params)}"
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.access_token}",
            },
        )

        return self._send(request)

    def post(self, path: str, params: Optional[dict] = None) -> dict:
        data = urlencode(dict(params or {})).encode("utf-8")
        url = f"{self.base_url}/{path.strip('/')}"
        request = Request(
            url,
            data=data,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )

        return self._send(request)


def resolve_instagram_account_id(client: InstagramClient, configured_id: str) -> str:
    """Return the Instagram user_id for an Instagram Login token."""
    configured_id = configured_id.strip()
    if not configured_id:
        response = client.get("me", {"fields": "user_id,username"})
        account_id = response.get("user_id")
        if not account_id:
            raise InstagramApiError(
                "Could not discover Instagram account ID from /me. Set "
                "INSTAGRAM_USER_ID in .env to your numeric Instagram professional "
                "account ID."
            )

        username = response.get("username", "unknown")
        print(f"Found Instagram account @{username}: {account_id}")
        return account_id

    if not configured_id.isdigit():
        raise InstagramApiError(
            "INSTAGRAM_USER_ID must be a numeric Instagram user_id "
            "ID, not a username. Remove "
            "INSTAGRAM_USER_ID from .env to auto-discover it, or replace it with "
            "the user_id returned by Instagram."
        )
    if configured_id:
        print(f"Using Instagram account: {configured_id}")
        return configured_id


def fetch_authenticated_account(client: InstagramClient) -> dict:
    """Return account identity fields for the current Instagram token."""
    return client.get("me", {"fields": "id,user_id,username"})


def _paged_items(client: InstagramClient, path: str, params: dict) -> Iterator[dict]:
    response = client.get(path, params)

    while True:
        for item in response.get("data", []):
            yield item

        next_url = response.get("paging", {}).get("next")
        if not next_url:
            break

        response = client.get(next_url)


def fetch_reels(client: InstagramClient, instagram_account_id: str) -> Iterator[dict]:
    """Yield Reel media for one Instagram professional account."""
    fields = ",".join(
        [
            "id",
            "caption",
            "media_type",
            "media_product_type",
            "permalink",
            "timestamp",
            "comments_count",
            "like_count",
        ]
    )
    for media in _paged_items(
        client,
        f"{instagram_account_id}/media",
        {"fields": fields, "limit": 100},
    ):
        if media.get("media_product_type") == "REELS":
            yield media


def fetch_media_comments(client: InstagramClient, media_id: str) -> Iterator[dict]:
    """Yield top-level comments for one Instagram media item."""
    fields = ",".join(
        [
            "id",
            "text",
            "username",
            "timestamp",
            "like_count",
        ]
    )
    yield from _paged_items(
        client,
        f"{media_id}/comments",
        {"fields": fields, "limit": 100},
    )


def fetch_comment(client: InstagramClient, comment_id: str) -> dict:
    """Fetch one Instagram comment node with richer fields than edges return."""
    fields = ",".join(
        [
            "id",
            "text",
            "from",
            "username",
            "user",
            "parent_id",
            "media",
            "timestamp",
            "like_count",
        ]
    )
    return client.get(comment_id, {"fields": fields})


def fetch_comment_replies(client: InstagramClient, comment_id: str) -> Iterator[dict]:
    """Yield replies for one Instagram comment."""
    fields = ",".join(
        [
            "id",
            "text",
            "username",
            "timestamp",
            "like_count",
        ]
    )
    for reply in _paged_items(
        client,
        f"{comment_id}/replies",
        {"fields": fields, "limit": 100},
    ):
        if reply.get("username") or reply.get("from") or not reply.get("id"):
            yield reply
            continue

        hydrated_reply = fetch_comment(client, reply["id"])
        hydrated_reply.setdefault("parent_id", comment_id)
        yield hydrated_reply


def post_reply_to_comment(
    client: InstagramClient, parent_comment_id: str, reply_text: str
) -> str:
    """Post a reply to an Instagram comment and return the reply ID."""
    response = client.post(
        f"{parent_comment_id}/replies",
        {"message": reply_text},
    )
    reply_id = response.get("id")
    if not reply_id:
        raise InstagramApiError(
            "Instagram accepted the reply but did not return a reply ID."
        )
    return reply_id


def caption_title(caption: Optional[str]) -> Optional[str]:
    if not caption:
        return None
    first_line = next((line.strip() for line in caption.splitlines() if line.strip()), "")
    if not first_line:
        return None
    return first_line[:140]


def instagram_comment_row(comment: dict, media: dict, is_reply: bool = False) -> dict:
    return {
        "instagram_comment_id": comment.get("id"),
        "instagram_media_id": media.get("id"),
        "parent_comment_id": comment.get("parent_comment_id"),
        "is_reply": is_reply,
        "media_type": media.get("media_type"),
        "media_product_type": media.get("media_product_type"),
        "media_permalink": media.get("permalink"),
        "video_title": caption_title(media.get("caption")) or "Instagram Reel",
        "media_caption": media.get("caption"),
        "author_name": comment.get("username"),
        "author_channel_id": comment.get("username"),
        "text": comment.get("text"),
        "like_count": comment.get("like_count", 0),
        "published_at": comment.get("timestamp"),
        "updated_at": comment.get("timestamp"),
    }
