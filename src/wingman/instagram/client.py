"""Small, dependency-free client for the Instagram Graph API."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class InstagramAPIError(RuntimeError):
    """An actionable Instagram Graph API failure."""


@dataclass(frozen=True)
class PostedReply:
    reply_id: str
    text: str
    published_at: str


class InstagramClient:
    def __init__(
        self,
        access_token: str,
        api_version: str = "v25.0",
        *,
        timeout: float = 30,
        retries: int = 3,
    ) -> None:
        if not access_token.strip():
            raise ValueError("Instagram access token cannot be empty.")
        self.access_token = access_token.strip()
        self.base_url = f"https://graph.instagram.com/{api_version.strip('/')}"
        self.timeout = timeout
        self.retries = retries

    def get(self, path_or_url: str, **parameters: object) -> dict[str, Any]:
        url = self._url(path_or_url, parameters)
        return self._request(Request(url, headers=self._headers()))

    def post(self, path: str, **parameters: object) -> dict[str, Any]:
        encoded = urlencode(parameters).encode()
        request = Request(
            self._url(path, {}),
            data=encoded,
            headers={
                **self._headers(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        return self._request(request, attempts=1)

    def send_message(self, account_id: str, recipient: dict[str, str], message: dict[str, Any]) -> dict[str, Any]:
        """Send once: retrying a timed-out DM could deliver it twice."""
        request = Request(
            self._url(f"{account_id}/messages", {}),
            data=json.dumps({"recipient": recipient, "message": message}).encode("utf-8"),
            headers={**self._headers(), "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except HTTPError as error:
            raise InstagramAPIError(
                self._error_message(error.read().decode("utf-8", errors="replace"), error.code)
            ) from error
        except (URLError, TimeoutError) as error:
            raise InstagramAPIError(f"Instagram message delivery is uncertain: {error}") from error
        if not isinstance(payload, dict) or not payload.get("message_id"):
            raise InstagramAPIError("Instagram did not confirm the message; delivery is uncertain.")
        return payload

    def iter_edge(
        self, path: str, *, fields: str, limit: int = 100
    ) -> Iterator[dict[str, Any]]:
        response = self.get(path, fields=fields, limit=limit)
        seen_urls: set[str] = set()
        while True:
            yield from response.get("data", [])
            next_url = response.get("paging", {}).get("next")
            if not next_url:
                return
            if next_url in seen_urls:
                raise InstagramAPIError("Instagram returned repeated pagination.")
            seen_urls.add(next_url)
            response = self.get(next_url)

    def authenticated_account(self) -> dict[str, Any]:
        return self.get("me", fields="id,user_id,username,account_type,media_count")

    def media(self, account_id: str) -> list[dict[str, Any]]:
        fields = (
            "id,caption,media_type,media_product_type,permalink,timestamp,"
            "thumbnail_url,media_url,comments_count"
        )
        return list(self.iter_edge(f"{account_id}/media", fields=fields))

    def comments(self, media_id: str) -> list[dict[str, Any]]:
        fields = "id,text,from,username,parent_id,timestamp,like_count"
        return list(self.iter_edge(f"{media_id}/comments", fields=fields))

    def reply(self, comment_id: str, text: str) -> PostedReply:
        reply_text = text.strip()
        if not reply_text:
            raise ValueError("Reply text cannot be empty.")
        response = self.post(f"{comment_id}/replies", message=reply_text)
        return PostedReply(
            reply_id=str(response["id"]),
            text=reply_text,
            published_at=datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        )

    def _request(self, request: Request, *, attempts: int | None = None) -> dict[str, Any]:
        limit = attempts if attempts is not None else self.retries
        for attempt in range(limit):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    raise InstagramAPIError("Instagram returned an invalid response.")
                return payload
            except HTTPError as error:
                body = error.read().decode("utf-8", errors="replace")
                message = self._error_message(body, error.code)
                if error.code not in {429, 500, 502, 503, 504}:
                    raise InstagramAPIError(message) from error
                final_error: Exception = InstagramAPIError(message)
            except (URLError, TimeoutError) as error:
                final_error = InstagramAPIError(
                    f"Instagram API request failed: {error}"
                )
            if attempt + 1 < limit:
                time.sleep(2**attempt)
        raise final_error

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }

    def _url(self, path_or_url: str, parameters: dict[str, object]) -> str:
        if path_or_url.startswith(("https://", "http://")):
            return path_or_url
        url = f"{self.base_url}/{path_or_url.lstrip('/')}"
        return f"{url}?{urlencode(parameters)}" if parameters else url

    @staticmethod
    def _error_message(body: str, status: int) -> str:
        try:
            error = json.loads(body).get("error", {})
            detail = error.get("message") or error.get("error_user_msg")
        except (json.JSONDecodeError, AttributeError):
            detail = None
        return f"Instagram API error {status}: {detail or 'unknown error'}"


def post_comment_reply(
    client: InstagramClient, parent_comment_id: str, text: str
) -> PostedReply:
    """Post one Instagram reply using the same service shape as YouTube."""
    return client.reply(parent_comment_id, text)
