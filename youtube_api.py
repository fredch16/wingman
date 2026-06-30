from pathlib import Path
from typing import Iterator, Optional

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
API_SERVICE_NAME = "youtube"
API_VERSION = "v3"


class YouTubeApiError(RuntimeError):
    """Raised when the YouTube API cannot complete the requested operation."""


def authenticate(client_secrets_file: str, token_file: str):
    """Authenticate with OAuth and return an authorized YouTube API client."""
    client_secrets_path = Path(client_secrets_file)
    token_path = Path(token_file)

    if not client_secrets_path.exists():
        raise FileNotFoundError(
            f"OAuth client secrets file not found: {client_secrets_path}"
        )

    credentials: Optional[Credentials] = None

    if token_path.exists():
        credentials = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    try:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())

        if not credentials or not credentials.valid:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(client_secrets_path), SCOPES
            )
            # In WSL, this prints a local URL that you can open in a Windows browser.
            credentials = flow.run_local_server(port=0)

        token_path.write_text(credentials.to_json(), encoding="utf-8")
    except RefreshError as exc:
        raise YouTubeApiError(
            "Could not refresh OAuth credentials. Delete token.json and run again."
        ) from exc

    return build(API_SERVICE_NAME, API_VERSION, credentials=credentials)


def get_authenticated_channel_id(youtube) -> str:
    """Return the channel ID for the authenticated YouTube account."""
    try:
        response = (
            youtube.channels()
            .list(part="id", mine=True, maxResults=1)
            .execute()
        )
    except HttpError as exc:
        raise YouTubeApiError(f"Could not fetch authenticated channel: {exc}") from exc

    items = response.get("items", [])
    if not items:
        raise YouTubeApiError(
            "No YouTube channel was found for the authenticated Google account."
        )

    return items[0]["id"]


def fetch_comment_threads(youtube, channel_id: str) -> Iterator[dict]:
    """Yield all comment thread resources related to a channel."""
    page_token = None

    while True:
        try:
            request = youtube.commentThreads().list(
                part="snippet,replies",
                allThreadsRelatedToChannelId=channel_id,
                maxResults=100,
                order="time",
                textFormat="plainText",
                pageToken=page_token,
            )
            response = request.execute()
        except HttpError as exc:
            raise YouTubeApiError(f"Could not fetch comment threads: {exc}") from exc

        for item in response.get("items", []):
            yield item

        page_token = response.get("nextPageToken")
        if not page_token:
            break


def fetch_video_title(youtube, video_id: str) -> Optional[str]:
    """Fetch a video's title, returning None if the video is unavailable."""
    try:
        response = (
            youtube.videos()
            .list(part="snippet", id=video_id, maxResults=1)
            .execute()
        )
    except HttpError as exc:
        raise YouTubeApiError(f"Could not fetch video title for {video_id}: {exc}") from exc

    items = response.get("items", [])
    if not items:
        return None

    return items[0].get("snippet", {}).get("title")


def _author_channel_id(comment_snippet: dict) -> Optional[str]:
    author_channel = comment_snippet.get("authorChannelId")
    if isinstance(author_channel, dict):
        return author_channel.get("value")
    return None


def extract_top_level_comment(thread: dict) -> dict:
    """Convert a YouTube comment thread resource into one database row."""
    thread_snippet = thread.get("snippet", {})
    top_level_comment = thread_snippet.get("topLevelComment", {})
    comment_snippet = top_level_comment.get("snippet", {})

    return {
        "youtube_comment_id": top_level_comment.get("id"),
        "youtube_thread_id": thread.get("id"),
        "video_id": thread_snippet.get("videoId"),
        "video_title": None,
        "author_name": comment_snippet.get("authorDisplayName"),
        "author_channel_id": _author_channel_id(comment_snippet),
        "text": comment_snippet.get("textDisplay"),
        "like_count": comment_snippet.get("likeCount", 0),
        "published_at": comment_snippet.get("publishedAt"),
        "updated_at": comment_snippet.get("updatedAt"),
    }
