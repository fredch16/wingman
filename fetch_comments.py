"""Fetch every top-level comment from configured YouTube videos."""

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

from comment_store import SyncSummary, connect_database, sync_comments

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


@dataclass(frozen=True)
class VideoSyncResult:
    video_id: str
    fetch_result: FetchResult | None = None
    sync_summary: SyncSummary | None = None
    error: str | None = None


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


def get_authenticated_youtube_client() -> Any:
    """Build an OAuth-authenticated YouTube client."""
    client_secrets_file = required_env("YOUTUBE_CLIENT_SECRETS_FILE")
    token_file = required_env("YOUTUBE_TOKEN_FILE")
    credentials = youtube_credentials(client_secrets_file, token_file)
    return build("youtube", "v3", credentials=credentials)


def configured_video_ids() -> list[str]:
    """Read, validate, and deduplicate configured video IDs."""
    configured = os.getenv("YOUTUBE_VIDEO_IDS", "").strip()
    if configured:
        raw_values = configured.split(",")
    else:
        legacy_keys = [
            key
            for key in os.environ
            if re.fullmatch(r"YOUTUBE_VIDEO_ID(?:_\d+)?", key)
        ]
        legacy_keys.sort(
            key=lambda key: (
                0 if key == "YOUTUBE_VIDEO_ID" else int(key.rsplit("_", 1)[1])
            )
        )
        raw_values = [
            value
            for key in legacy_keys
            for value in os.environ[key].split(",")
        ]

    video_ids = list(dict.fromkeys(value.strip() for value in raw_values if value.strip()))
    if not video_ids:
        raise ValueError("Missing required environment variable: YOUTUBE_VIDEO_IDS")
    invalid_ids = [
        video_id
        for video_id in video_ids
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id)
    ]
    if invalid_ids:
        raise ValueError(
            "Every configured YouTube video ID must be 11 characters, not a URL."
        )
    return video_ids


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


def page_error_message(error: PageFetchError) -> str:
    if isinstance(error.cause, HttpError):
        return f"Page {error.page_number} failed: {api_error_message(error.cause)}"
    return f"Page {error.page_number} failed: {error.cause}"


def sync_videos(
    youtube: Any,
    video_ids: list[str],
    connection: sqlite3.Connection,
) -> list[VideoSyncResult]:
    """Fetch and persist each configured video without aborting later videos."""
    results = []
    for video_id in video_ids:
        print(f"Video: {video_id}")
        try:
            fetch_result = fetch_all_comments_for_video(youtube, video_id)
            sync_summary = sync_comments(connection, fetch_result.comments)
            results.append(
                VideoSyncResult(
                    video_id=video_id,
                    fetch_result=fetch_result,
                    sync_summary=sync_summary,
                )
            )
        except PageFetchError as error:
            results.append(VideoSyncResult(video_id, error=page_error_message(error)))
        except PaginationError as error:
            results.append(VideoSyncResult(video_id, error=f"Pagination error: {error}"))
    return results


def print_sync_results(results: list[VideoSyncResult]) -> None:
    """Print per-video details followed by an aggregate summary."""
    for result in results:
        print(f"\nVideo summary: {result.video_id}")
        if result.error:
            print(f"Status: Failed")
            print(f"Error: {result.error}")
            continue

        assert result.fetch_result is not None
        assert result.sync_summary is not None
        print("Status: Completed")
        print(f"Pages fetched: {result.fetch_result.pages_fetched}")
        print(f"Fetched: {result.sync_summary.fetched}")
        print(f"Newly inserted: {result.sync_summary.newly_inserted}")
        print(f"Updated: {result.sync_summary.updated}")
        print(f"Unchanged: {result.sync_summary.unchanged}")
        if not result.fetch_result.comments:
            print("No comments returned for this video.")

    successful = [result for result in results if not result.error]
    summaries = [
        result.sync_summary for result in successful if result.sync_summary is not None
    ]
    print("\nOverall summary")
    print(f"Videos configured: {len(results)}")
    print(f"Videos completed: {len(successful)}")
    print(f"Videos failed: {len(results) - len(successful)}")
    print(f"Fetched: {sum(summary.fetched for summary in summaries)}")
    print(f"Newly inserted: {sum(summary.newly_inserted for summary in summaries)}")
    print(f"Updated: {sum(summary.updated for summary in summaries)}")
    print(f"Unchanged: {sum(summary.unchanged for summary in summaries)}\n")

    for result in successful:
        assert result.fetch_result is not None
        if result.fetch_result.comments:
            print(f"Comments for video {result.video_id}")
            print_comments(result.fetch_result.comments)


def main() -> int:
    load_dotenv()
    try:
        video_ids = configured_video_ids()
        youtube = get_authenticated_youtube_client()
        database_path = os.getenv("DATABASE_PATH", "comments.db").strip() or "comments.db"
        with connect_database(database_path) as connection:
            results = sync_videos(youtube, video_ids, connection)
    except ValueError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 1
    except RefreshError as error:
        print(f"OAuth token refresh failed: {error}", file=sys.stderr)
        return 1
    except TransportError as error:
        print(f"Network error while authenticating with Google: {error}", file=sys.stderr)
        return 1
    except sqlite3.Error as error:
        print(f"SQLite error while saving comments: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"Network error while contacting YouTube: {error}", file=sys.stderr)
        return 1

    print("\nFinished")
    print_sync_results(results)
    return 1 if all(result.error for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
