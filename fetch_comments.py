"""Fetch every top-level comment from one YouTube video."""

import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from comment_store import connect_database, sync_comments

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]


@dataclass(frozen=True)
class Comment:
    comment_id: str
    thread_id: str
    video_id: str
    author_display_name: str
    author_channel_id: str | None
    text: str
    like_count: int
    published_at: str
    updated_at: str
    total_reply_count: int
    can_reply: bool
    is_public: bool


@dataclass(frozen=True)
class FetchResult:
    comments: list[Comment]
    pages_fetched: int


class PaginationError(RuntimeError):
    """Raised when YouTube returns an unsafe pagination sequence."""


class PageFetchError(RuntimeError):
    """Raised when a particular comment page cannot be fetched."""

    def __init__(self, page_number: int, cause: Exception) -> None:
        self.page_number = page_number
        self.cause = cause
        super().__init__(f"Page {page_number} failed: {cause}")


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def api_error_message(error: HttpError) -> str:
    reason = ""
    try:
        details = error.error_details
        if details:
            reason = details[0].get("reason", "")
    except (AttributeError, IndexError, TypeError):
        pass

    if reason in {"videoNotFound", "invalidVideoId"} or error.resp.status == 404:
        return "Invalid video ID or video not found."
    if reason == "commentsDisabled":
        return "Comments are disabled for this video."
    if reason in {
        "quotaExceeded",
        "dailyLimitExceeded",
        "dailyLimitExceededUnreg",
        "rateLimitExceeded",
        "userRateLimitExceeded",
    } or error.resp.status == 429:
        return "YouTube API quota or rate limit exceeded."
    if error.resp.status in {401, 403}:
        return f"YouTube API authorization error ({reason or 'access denied'})."
    return f"YouTube API error {error.resp.status} ({reason or 'unknown error'})."


def youtube_credentials(client_secrets_file: str, token_file: str) -> Credentials:
    """Load, refresh, or create OAuth credentials for YouTube."""
    client_secrets_path = Path(client_secrets_file)
    token_path = Path(token_file)
    if not client_secrets_path.is_file():
        raise ValueError(f"OAuth client secrets file not found: {client_secrets_file}")

    credentials = None
    if token_path.is_file():
        credentials = Credentials.from_authorized_user_file(token_path)

    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())

    if not credentials or not credentials.valid:
        flow = InstalledAppFlow.from_client_secrets_file(client_secrets_path, SCOPES)
        credentials = flow.run_local_server(port=0)
        token_path.write_text(credentials.to_json(), encoding="utf-8")

    return credentials


def get_authenticated_youtube_client() -> tuple[Any, str]:
    """Build an OAuth-authenticated YouTube client and return its video ID."""
    client_secrets_file = required_env("YOUTUBE_CLIENT_SECRETS_FILE")
    token_file = required_env("YOUTUBE_TOKEN_FILE")
    video_id = required_env("YOUTUBE_VIDEO_ID")
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise ValueError(
            "YOUTUBE_VIDEO_ID must be an 11-character video ID, not a URL."
        )
    credentials = youtube_credentials(client_secrets_file, token_file)
    return build("youtube", "v3", credentials=credentials), video_id


def fetch_comment_page(
    youtube: Any, video_id: str, page_token: str | None = None
) -> dict[str, Any]:
    """Fetch one page of top-level comment threads."""
    request = youtube.commentThreads().list(
        part="snippet",
        videoId=video_id,
        maxResults=100,
        order="time",
        textFormat="plainText",
        pageToken=page_token,
    )
    return request.execute()


def comment_from_thread(thread: dict[str, Any]) -> Comment:
    """Convert one API comment thread into the project's comment model."""
    thread_snippet = thread["snippet"]
    top_level_comment = thread_snippet["topLevelComment"]
    comment_snippet = top_level_comment["snippet"]
    author_channel = comment_snippet.get("authorChannelId")
    return Comment(
        comment_id=top_level_comment["id"],
        thread_id=thread["id"],
        video_id=thread_snippet["videoId"],
        author_display_name=comment_snippet.get(
            "authorDisplayName", "Unknown author"
        ),
        author_channel_id=author_channel.get("value") if author_channel else None,
        text=comment_snippet.get("textDisplay", ""),
        like_count=comment_snippet.get("likeCount", 0),
        published_at=comment_snippet.get("publishedAt", ""),
        updated_at=comment_snippet.get("updatedAt", ""),
        total_reply_count=thread_snippet.get("totalReplyCount", 0),
        can_reply=thread_snippet.get("canReply", False),
        is_public=thread_snippet.get("isPublic", False),
    )


def fetch_all_comments_for_video(youtube: Any, video_id: str) -> FetchResult:
    """Fetch and deduplicate every available top-level comment."""
    comments_by_id: dict[str, Comment] = {}
    seen_page_tokens: set[str] = set()
    page_token: str | None = None
    page_number = 1

    while True:
        print(f"Fetching page {page_number}...")
        try:
            response = fetch_comment_page(youtube, video_id, page_token)
        except Exception as error:
            raise PageFetchError(page_number, error) from error

        items = response.get("items", [])
        print(f"Fetched {len(items)} comments\n")
        for thread in items:
            comment = comment_from_thread(thread)
            comments_by_id.setdefault(comment.comment_id, comment)

        next_page_token = response.get("nextPageToken")
        if not next_page_token:
            return FetchResult(list(comments_by_id.values()), page_number)
        if next_page_token in seen_page_tokens:
            raise PaginationError(
                f"Repeated nextPageToken detected after page {page_number}; "
                "stopping to avoid an infinite loop."
            )

        seen_page_tokens.add(next_page_token)
        page_token = next_page_token
        page_number += 1


def print_comments(comments: list[Comment]) -> None:
    """Print comments in the existing readable terminal format."""
    for comment in comments:
        print(f"[{comment.like_count} likes] {comment.author_display_name}")
        print(comment.text)
        print(f"Published: {comment.published_at or 'Unknown'}")
        print(f"Replies: {comment.total_reply_count}")
        print(f"ID: {comment.comment_id}")
        print("-" * 50)


def main() -> int:
    load_dotenv()
    try:
        youtube, video_id = get_authenticated_youtube_client()
        result = fetch_all_comments_for_video(youtube, video_id)
        database_path = os.getenv("DATABASE_PATH", "comments.db").strip() or "comments.db"
        with connect_database(database_path) as connection:
            sync_summary = sync_comments(connection, result.comments)
    except ValueError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 1
    except RefreshError as error:
        print(f"OAuth token refresh failed: {error}", file=sys.stderr)
        return 1
    except TransportError as error:
        print(f"Network error while authenticating with Google: {error}", file=sys.stderr)
        return 1
    except PageFetchError as error:
        if isinstance(error.cause, HttpError):
            detail = api_error_message(error.cause)
        else:
            detail = str(error.cause)
        print(f"Page {error.page_number} failed: {detail}", file=sys.stderr)
        return 1
    except PaginationError as error:
        print(f"Pagination error: {error}", file=sys.stderr)
        return 1
    except sqlite3.Error as error:
        print(f"SQLite error while saving comments: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"Network error while contacting YouTube: {error}", file=sys.stderr)
        return 1

    print("Finished")
    print(f"Video ID: {video_id}")
    print(f"Pages fetched: {result.pages_fetched}")
    print(f"Total unique comments: {len(result.comments)}\n")
    print("Sync summary")
    print(f"Fetched: {sync_summary.fetched}")
    print(f"Newly inserted: {sync_summary.newly_inserted}")
    print(f"Updated: {sync_summary.updated}")
    print(f"Unchanged: {sync_summary.unchanged}\n")

    if not result.comments:
        print("No comments returned for this video.")
        return 0

    print_comments(result.comments)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
