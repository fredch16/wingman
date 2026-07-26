"""Discover and persist creator videos across supported platforms."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass(frozen=True)
class Video:
    video_id: str
    title: str
    published_at: str
    thumbnail_url: str
    platform: str = "youtube"
    permalink: str = ""


@dataclass(frozen=True)
class VideoDiscoveryResult:
    videos: list[Video]
    pages_fetched: int


@dataclass(frozen=True)
class VideoStoreSummary:
    discovered: int
    newly_inserted: int
    updated: int
    unchanged: int


@dataclass(frozen=True)
class StoredVideo:
    video_id: str
    title: str
    published_at: str
    thumbnail_url: str
    is_enabled: bool
    first_seen_at: str
    last_seen_at: str
    comments_last_checked_at: str | None
    summary: str | None
    platform: str
    permalink: str


class PlaylistPaginationError(RuntimeError):
    """Raised when playlist pagination would repeat indefinitely."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def get_uploads_playlist_id(youtube: Any) -> str:
    """Return the uploads playlist ID for the authenticated channel."""
    response = youtube.channels().list(part="contentDetails", mine=True).execute()
    items = response.get("items", [])
    if not items:
        raise RuntimeError("No authenticated YouTube channel was returned.")
    try:
        return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    except KeyError as error:
        raise RuntimeError("The channel response has no uploads playlist.") from error


def video_from_playlist_item(item: dict[str, Any]) -> Video:
    snippet = item["snippet"]
    thumbnails = snippet.get("thumbnails", {})
    thumbnail_url = ""
    for size in ("maxres", "standard", "high", "medium", "default"):
        if size in thumbnails:
            thumbnail_url = thumbnails[size].get("url", "")
            break
    return Video(
        video_id=snippet["resourceId"]["videoId"],
        title=snippet.get("title", ""),
        published_at=item.get("contentDetails", {}).get(
            "videoPublishedAt", snippet.get("publishedAt", "")
        ),
        thumbnail_url=thumbnail_url,
    )


def fetch_all_upload_videos(youtube: Any) -> VideoDiscoveryResult:
    """Fetch every item from the authenticated channel's uploads playlist."""
    playlist_id = get_uploads_playlist_id(youtube)
    videos_by_id: dict[str, Video] = {}
    page_token: str | None = None
    seen_page_tokens: set[str] = set()
    page_number = 1

    while True:
        print(f"Discovering uploads page {page_number}...")
        response = (
            youtube.playlistItems()
            .list(
                part="snippet,contentDetails",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=page_token,
            )
            .execute()
        )
        items = response.get("items", [])
        print(f"Discovered {len(items)} videos\n")
        for item in items:
            video = video_from_playlist_item(item)
            videos_by_id.setdefault(video.video_id, video)

        next_page_token = response.get("nextPageToken")
        if not next_page_token:
            return VideoDiscoveryResult(list(videos_by_id.values()), page_number)
        if next_page_token in seen_page_tokens:
            raise PlaylistPaginationError(
                f"Repeated uploads nextPageToken after page {page_number}."
            )
        seen_page_tokens.add(next_page_token)
        page_token = next_page_token
        page_number += 1


def create_videos_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS videos (
            video_id TEXT PRIMARY KEY,
            platform TEXT NOT NULL DEFAULT 'youtube',
            permalink TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL,
            published_at TEXT NOT NULL,
            thumbnail_url TEXT NOT NULL,
            is_enabled INTEGER NOT NULL DEFAULT 1,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            comments_last_checked_at TEXT
            , summary TEXT
        )
        """
    )
    existing_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(videos)")
    }
    if "comments_last_checked_at" not in existing_columns:
        connection.execute(
            "ALTER TABLE videos ADD COLUMN comments_last_checked_at TEXT"
        )
    if "summary" not in existing_columns:
        connection.execute("ALTER TABLE videos ADD COLUMN summary TEXT")
    if "platform" not in existing_columns:
        connection.execute(
            "ALTER TABLE videos ADD COLUMN platform TEXT NOT NULL DEFAULT 'youtube'"
        )
    if "permalink" not in existing_columns:
        connection.execute(
            "ALTER TABLE videos ADD COLUMN permalink TEXT NOT NULL DEFAULT ''"
        )


def store_discovered_videos(
    connection: sqlite3.Connection,
    videos: list[Video],
    discovered_at: str | None = None,
) -> VideoStoreSummary:
    """Upsert video metadata while preserving the existing enabled state."""
    observed_at = discovered_at or utc_now()
    unique_videos = {video.video_id: video for video in videos}
    inserted = 0
    updated = 0
    unchanged = 0

    create_videos_table(connection)
    with connection:
        for video in unique_videos.values():
            existing = connection.execute(
                """
                SELECT title, published_at, thumbnail_url, platform, permalink
                FROM videos WHERE video_id = ?
                """,
                (video.video_id,),
            ).fetchone()
            current_values = (
                video.title,
                video.published_at,
                video.thumbnail_url,
                video.platform,
                video.permalink,
            )
            if existing is None:
                inserted += 1
            elif tuple(existing) == current_values:
                unchanged += 1
            else:
                updated += 1

            connection.execute(
                """
                INSERT INTO videos (
                    video_id, platform, permalink, title, published_at,
                    thumbnail_url, is_enabled,
                    first_seen_at, last_seen_at
                ) VALUES (
                    :video_id, :platform, :permalink, :title, :published_at,
                    :thumbnail_url, 1,
                    :first_seen_at, :last_seen_at
                )
                ON CONFLICT(video_id) DO UPDATE SET
                    platform = excluded.platform,
                    permalink = excluded.permalink,
                    title = excluded.title,
                    published_at = excluded.published_at,
                    thumbnail_url = excluded.thumbnail_url,
                    last_seen_at = excluded.last_seen_at
                """,
                {
                    **asdict(video),
                    "first_seen_at": observed_at,
                    "last_seen_at": observed_at,
                },
            )

    return VideoStoreSummary(
        discovered=len(unique_videos),
        newly_inserted=inserted,
        updated=updated,
        unchanged=unchanged,
    )


def list_stored_videos(connection: sqlite3.Connection) -> list[StoredVideo]:
    create_videos_table(connection)
    rows = connection.execute(
        """
        SELECT video_id, title, published_at, thumbnail_url, is_enabled,
               first_seen_at, last_seen_at, comments_last_checked_at
               , summary, platform, permalink
        FROM videos
        ORDER BY published_at DESC, video_id
        """
    ).fetchall()
    return [
        StoredVideo(
            video_id=row["video_id"],
            title=row["title"],
            published_at=row["published_at"],
            thumbnail_url=row["thumbnail_url"],
            is_enabled=bool(row["is_enabled"]),
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            comments_last_checked_at=row["comments_last_checked_at"],
            summary=row["summary"],
            platform=row["platform"],
            permalink=row["permalink"],
        )
        for row in rows
    ]


def enabled_video_ids(connection: sqlite3.Connection) -> list[str]:
    return [
        video.video_id for video in list_stored_videos(connection) if video.is_enabled
    ]


def set_video_enabled(
    connection: sqlite3.Connection, video_id: str, is_enabled: bool
) -> bool:
    create_videos_table(connection)
    with connection:
        cursor = connection.execute(
            "UPDATE videos SET is_enabled = ? WHERE video_id = ?",
            (int(is_enabled), video_id),
        )
    return cursor.rowcount > 0


def update_video_summary(
    connection: sqlite3.Connection, video_id: str, summary: str | None
) -> bool:
    create_videos_table(connection)
    normalized_summary = summary.strip() if summary else None
    with connection:
        cursor = connection.execute(
            "UPDATE videos SET summary = ? WHERE video_id = ?",
            (normalized_summary or None, video_id),
        )
    return cursor.rowcount > 0


def append_video_response_guidance(
    connection: sqlite3.Connection,
    video_id: str,
    guidance: str,
) -> bool:
    create_videos_table(connection)
    normalized = " ".join(guidance.split()).strip()
    if not normalized:
        raise ValueError("Video response guidance cannot be empty.")
    row = connection.execute(
        "SELECT summary FROM videos WHERE video_id = ?",
        (video_id,),
    ).fetchone()
    if row is None:
        return False
    current = (row["summary"] or "").strip()
    if normalized.casefold() in current.casefold():
        return True
    heading = "## Response Guidance"
    if heading in current:
        updated = f"{current}\n\n- {normalized}"
    elif current:
        updated = f"{current}\n\n{heading}\n\n- {normalized}"
    else:
        updated = f"{heading}\n\n- {normalized}"
    return update_video_summary(connection, video_id, updated)


def comments_checked_recently(
    connection: sqlite3.Connection,
    video_id: str,
    now: datetime | None = None,
    freshness: timedelta = timedelta(hours=1),
) -> bool:
    create_videos_table(connection)
    row = connection.execute(
        "SELECT comments_last_checked_at FROM videos WHERE video_id = ?",
        (video_id,),
    ).fetchone()
    if not row or not row["comments_last_checked_at"]:
        return False
    checked_at = datetime.fromisoformat(
        row["comments_last_checked_at"].replace("Z", "+00:00")
    )
    return (now or datetime.now(timezone.utc)) - checked_at < freshness


def mark_comments_checked(
    connection: sqlite3.Connection,
    video_id: str,
    checked_at: str | None = None,
) -> None:
    create_videos_table(connection)
    with connection:
        connection.execute(
            """
            UPDATE videos
            SET comments_last_checked_at = ?
            WHERE video_id = ?
            """,
            (checked_at or utc_now(), video_id),
        )
