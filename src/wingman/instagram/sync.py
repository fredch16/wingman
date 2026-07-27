"""Discover Instagram media and synchronize comments into the Wingman inbox."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import datetime, timedelta

from dotenv import load_dotenv

from wingman.db.comment_store import connect_database, sync_comments
from wingman.db.video_catalog import (
    Video,
    comments_checked_recently,
    mark_comments_checked,
    store_discovered_videos,
)
from wingman.instagram.client import InstagramClient


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
    platform: str = "instagram"


def client_from_env() -> InstagramClient:
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip()
    if not token:
        raise ValueError(
            "Missing required environment variable: INSTAGRAM_ACCESS_TOKEN"
        )
    return InstagramClient(
        token,
        os.getenv("INSTAGRAM_GRAPH_API_VERSION", "v25.0").strip() or "v25.0",
    )


def account_id_from_env(client: InstagramClient) -> tuple[str, str]:
    account = client.authenticated_account()
    authenticated_ids = {
        str(value) for value in (account.get("user_id"), account.get("id")) if value
    }
    configured_id = (
        os.getenv("INSTAGRAM_ACCOUNT_ID", "").strip()
        or os.getenv("INSTAGRAM_USER_ID", "").strip()
    )
    account_id = (
        configured_id
        if configured_id in authenticated_ids
        else str(account.get("user_id") or account["id"])
    )
    return account_id, str(account.get("username", ""))


def video_from_media(media: dict[str, object]) -> Video:
    caption = str(media.get("caption") or "").strip()
    title = caption.splitlines()[0][:120] if caption else "Instagram post"
    return Video(
        video_id=str(media["id"]),
        title=title,
        published_at=str(media.get("timestamp") or ""),
        thumbnail_url=str(
            media.get("thumbnail_url") or media.get("media_url") or ""
        ),
        platform="instagram",
        permalink=str(media.get("permalink") or ""),
    )


def comment_from_api(
    item: dict[str, object], media_id: str, reply_count: int = 0
) -> Comment:
    author = item.get("from") if isinstance(item.get("from"), dict) else {}
    username = str(
        (author or {}).get("username") or item.get("username") or "Instagram user"
    )
    timestamp = str(item.get("timestamp") or "")
    return Comment(
        comment_id=str(item["id"]),
        thread_id=str(item["id"]),
        video_id=media_id,
        author_display_name=f"@{username.lstrip('@')}",
        author_channel_id=(
            str((author or {}).get("id")) if (author or {}).get("id") else None
        ),
        text=str(item.get("text") or ""),
        like_count=int(item.get("like_count") or 0),
        published_at=timestamp,
        updated_at=timestamp,
        total_reply_count=reply_count,
        can_reply=True,
        is_public=True,
    )


def creator_reply(
    replies: list[dict[str, object]], creator_username: str
) -> dict[str, object] | None:
    for reply in replies:
        author = reply.get("from") if isinstance(reply.get("from"), dict) else {}
        username = str(author.get("username") or reply.get("username") or "")
        if username.casefold().lstrip("@") == creator_username.casefold().lstrip("@"):
            return reply
    return None


def group_comment_threads(
    items: list[dict[str, object]],
) -> tuple[list[dict[str, object]], dict[str, list[dict[str, object]]]]:
    """Return top-level comments and every descendant grouped under its root."""
    by_id = {str(item["id"]): item for item in items}
    roots: list[dict[str, object]] = []
    replies_by_root: dict[str, list[dict[str, object]]] = {}

    for item in items:
        parent_id = str(item.get("parent_id") or "")
        if not parent_id:
            roots.append(item)
            replies_by_root.setdefault(str(item["id"]), [])
            continue

        root_id = parent_id
        seen = {str(item["id"])}
        while root_id in by_id and by_id[root_id].get("parent_id"):
            if root_id in seen:
                break
            seen.add(root_id)
            root_id = str(by_id[root_id]["parent_id"])
        replies_by_root.setdefault(root_id, []).append(item)

    return roots, replies_by_root


def sync_instagram(
    client: InstagramClient,
    database_path: str,
    *,
    media_ids: set[str] | None = None,
    refresh: bool = False,
    freshness: timedelta = timedelta(hours=1),
    now: datetime | None = None,
) -> tuple[int, int]:
    account_id, creator_username = account_id_from_env(client)
    media = client.media(account_id)
    if media_ids:
        media = [item for item in media if str(item["id"]) in media_ids]

    connection = connect_database(database_path)
    comment_count = 0
    try:
        store_discovered_videos(
            connection, [video_from_media(item) for item in media]
        )
        for item in media:
            media_id = str(item["id"])
            if not refresh and comments_checked_recently(
                connection,
                media_id,
                now=now,
                freshness=freshness,
            ):
                minutes = int(freshness.total_seconds() // 60)
                print(
                    f"Instagram {media_id}: skipped "
                    f"(checked within {minutes} minutes; use --refresh)"
                )
                continue
            api_comments = client.comments(media_id)
            roots, replies_by_root = group_comment_threads(api_comments)
            comments = [
                comment_from_api(
                    comment,
                    media_id,
                    len(replies_by_root.get(str(comment["id"]), [])),
                )
                for comment in roots
            ]
            sync_comments(connection, comments)
            comment_count += len(comments)
            for source in roots:
                reply = creator_reply(
                    replies_by_root.get(str(source["id"]), []),
                    creator_username,
                )
                if reply is None:
                    continue
                timestamp = str(reply.get("timestamp") or "")
                connection.execute(
                    """
                    UPDATE comments
                    SET has_creator_reply = 1,
                        creator_reply_id = ?,
                        creator_replied_at = ?,
                        reply_status_checked_at = ?
                    WHERE comment_id = ?
                    """,
                    (str(reply["id"]), timestamp, timestamp, str(source["id"])),
                )
            mark_comments_checked(connection, media_id)
            connection.commit()
            print(f"Instagram {media_id}: synchronized {len(comments)} comments")
    finally:
        connection.close()
    return len(media), comment_count


def main(argv: list[str] | None = None) -> int:
    load_dotenv(".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=None)
    parser.add_argument("--media-id", action="append", default=[])
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="ignore freshness checks and fetch every selected media item",
    )
    parser.add_argument(
        "--freshness-minutes",
        type=int,
        default=int(os.getenv("INSTAGRAM_SYNC_FRESHNESS_MINUTES", "60")),
        help="skip media checked within this many minutes (default: 60)",
    )
    parser.add_argument(
        "--list-media", action="store_true", help="List media without syncing comments."
    )
    args = parser.parse_args(argv)
    if args.freshness_minutes <= 0:
        parser.error("--freshness-minutes must be greater than zero")
    client = client_from_env()
    account_id, username = account_id_from_env(client)
    media = client.media(account_id)
    if args.list_media:
        print(f"@{username}: {len(media)} media")
        for item in media:
            print(f"{item['id']}  {video_from_media(item).title}")
        return 0
    database = args.database or os.getenv("DATABASE_PATH", "comments.db")
    count, comments = sync_instagram(
        client,
        database,
        media_ids=set(args.media_id) or None,
        refresh=args.refresh,
        freshness=timedelta(minutes=args.freshness_minutes),
    )
    print(f"Synchronized {comments} comments from {count} Instagram media.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
