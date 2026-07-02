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

    return build(
        API_SERVICE_NAME,
        API_VERSION,
        credentials=credentials,
        cache_discovery=False,
    )


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


def fetch_comment_threads(
    youtube, channel_id: str, video_id: Optional[str] = None
) -> Iterator[dict]:
    """Yield comment thread resources for a channel, or for one video."""
    page_token = None

    while True:
        try:
            list_params = {
                "part": "snippet,replies",
                "maxResults": 100,
                "order": "time",
                "textFormat": "plainText",
                "pageToken": page_token,
            }

            if video_id:
                list_params["videoId"] = video_id
            else:
                list_params["allThreadsRelatedToChannelId"] = channel_id

            request = youtube.commentThreads().list(
                **list_params,
            )
            response = request.execute()
        except HttpError as exc:
            raise YouTubeApiError(f"Could not fetch comment threads: {exc}") from exc

        for item in response.get("items", []):
            yield item

        page_token = response.get("nextPageToken")
        if not page_token:
            break


def fetch_comment_replies(youtube, parent_comment_id: str) -> Iterator[dict]:
    """Yield all replies for one top-level comment."""
    page_token = None

    while True:
        try:
            response = (
                youtube.comments()
                .list(
                    part="snippet",
                    parentId=parent_comment_id,
                    maxResults=100,
                    textFormat="plainText",
                    pageToken=page_token,
                )
                .execute()
            )
        except HttpError as exc:
            raise YouTubeApiError(
                f"Could not fetch replies for {parent_comment_id}: {exc}"
            ) from exc

        for item in response.get("items", []):
            yield item

        page_token = response.get("nextPageToken")
        if not page_token:
            break


def post_reply_to_comment(youtube, parent_comment_id: str, reply_text: str) -> str:
    """Post a reply to a top-level YouTube comment and return the reply ID."""
    try:
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
    except HttpError as exc:
        raise YouTubeApiError(f"Could not post reply: {exc}") from exc

    reply_id = response.get("id")
    if not reply_id:
        raise YouTubeApiError("YouTube accepted the reply but did not return a reply ID.")

    return reply_id


def delete_comment(youtube, comment_id: str) -> None:
    """Delete a YouTube comment by ID."""
    try:
        youtube.comments().delete(id=comment_id).execute()
    except HttpError as exc:
        raise YouTubeApiError(f"Could not delete YouTube comment: {exc}") from exc


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


def _comment_row(
    comment_id: Optional[str],
    comment_snippet: dict,
    thread_id: Optional[str],
    video_id: Optional[str],
    is_reply: bool,
) -> dict:
    return {
        "youtube_comment_id": comment_id,
        "youtube_thread_id": thread_id,
        "parent_comment_id": comment_snippet.get("parentId"),
        "is_reply": is_reply,
        "video_id": video_id,
        "video_title": None,
        "author_name": comment_snippet.get("authorDisplayName"),
        "author_channel_id": _author_channel_id(comment_snippet),
        "text": comment_snippet.get("textDisplay"),
        "like_count": comment_snippet.get("likeCount", 0),
        "published_at": comment_snippet.get("publishedAt"),
        "updated_at": comment_snippet.get("updatedAt"),
    }


def extract_top_level_comment(thread: dict) -> dict:
    """Convert a YouTube comment thread resource into one database row."""
    thread_snippet = thread.get("snippet", {})
    top_level_comment = thread_snippet.get("topLevelComment", {})
    comment_snippet = top_level_comment.get("snippet", {})

    return _comment_row(
        comment_id=top_level_comment.get("id"),
        comment_snippet=comment_snippet,
        thread_id=thread.get("id"),
        video_id=thread_snippet.get("videoId"),
        is_reply=False,
    )


def extract_reply_comment(reply: dict, thread: dict) -> dict:
    """Convert a YouTube reply resource into one database row."""
    thread_snippet = thread.get("snippet", {})
    reply_snippet = reply.get("snippet", {})

    return _comment_row(
        comment_id=reply.get("id"),
        comment_snippet=reply_snippet,
        thread_id=thread.get("id"),
        video_id=reply_snippet.get("videoId") or thread_snippet.get("videoId"),
        is_reply=True,
    )


def total_reply_count(thread: dict) -> int:
    """Return the reply count reported on a comment thread."""
    return int(thread.get("snippet", {}).get("totalReplyCount", 0))
