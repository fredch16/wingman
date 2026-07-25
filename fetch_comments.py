"""Fetch the latest top-level comments from one YouTube video."""

import os
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]


def required_env(name: str) -> str:
    """Return a required environment variable or exit with a clear message."""
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def api_error_message(error: HttpError) -> str:
    """Turn common YouTube API errors into concise, actionable messages."""
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


def fetch_comments(
    credentials: Credentials, video_id: str
) -> list[dict[str, Any]]:
    """Fetch the first page of newest comment threads."""
    youtube = build("youtube", "v3", credentials=credentials)
    response = (
        youtube.commentThreads()
        .list(
            part="snippet",
            videoId=video_id,
            maxResults=100,
            order="time",
            textFormat="plainText",
        )
        .execute()
    )
    return response.get("items", [])


def print_comments(items: list[dict[str, Any]]) -> None:
    """Print top-level comment details in a readable terminal format."""
    print(f"Fetched {len(items)} comments\n")
    for thread in items:
        snippet = thread["snippet"]
        comment = snippet["topLevelComment"]
        comment_snippet = comment["snippet"]
        print(f"[{comment_snippet.get('likeCount', 0)} likes] "
              f"{comment_snippet.get('authorDisplayName', 'Unknown author')}")
        print(comment_snippet.get("textDisplay", ""))
        print(f"Published: {comment_snippet.get('publishedAt', 'Unknown')}")
        print(f"Replies: {snippet.get('totalReplyCount', 0)}")
        print(f"ID: {comment.get('id', 'Unknown')}")
        print("-" * 50)


def main() -> int:
    load_dotenv()
    try:
        client_secrets_file = required_env("YOUTUBE_CLIENT_SECRETS_FILE")
        token_file = required_env("YOUTUBE_TOKEN_FILE")
        video_id = required_env("YOUTUBE_VIDEO_ID")
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ValueError(
                "YOUTUBE_VIDEO_ID must be an 11-character video ID, not a URL."
            )
        credentials = youtube_credentials(client_secrets_file, token_file)
        items = fetch_comments(credentials, video_id)
    except ValueError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 1
    except RefreshError as error:
        print(f"OAuth token refresh failed: {error}", file=sys.stderr)
        return 1
    except TransportError as error:
        print(f"Network error while authenticating with Google: {error}", file=sys.stderr)
        return 1
    except HttpError as error:
        print(api_error_message(error), file=sys.stderr)
        return 1
    except OSError as error:
        print(f"Network error while contacting YouTube: {error}", file=sys.stderr)
        return 1

    if not items:
        print("No comments returned for this video.")
        return 0

    print_comments(items)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
