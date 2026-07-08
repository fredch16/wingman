import argparse
import os
import sys

from dotenv import load_dotenv

from database import (
    connect,
    count_instagram_comments,
    initialize_database,
    is_sync_cache_fresh,
    mark_sync_completed,
    reconcile_instagram_external_reply_status,
    upsert_instagram_comment,
)
from instagram_api import (
    InstagramApiError,
    InstagramClient,
    fetch_authenticated_account,
    fetch_comment_replies,
    fetch_media_comments,
    fetch_reels,
    instagram_comment_row,
    resolve_instagram_account_id,
)


def env_value(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def csv_env_values(name: str) -> list[str]:
    return [value.strip() for value in os.getenv(name, "").split(",") if value.strip()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync Instagram Reel comments into SQLite."
    )
    parser.add_argument(
        "--media-id",
        default=env_value("INSTAGRAM_MEDIA_ID", ""),
        help="Only sync comments for this Instagram media/Reel ID.",
    )
    parser.add_argument(
        "--list-media",
        action="store_true",
        help="Print Reel metadata from Instagram and exit without syncing comments.",
    )
    parser.add_argument(
        "--override-cache",
        action="store_true",
        help="Run the sync even when the local sync cache is still fresh.",
    )
    return parser.parse_args()


def env_int(name: str, default: int) -> int:
    try:
        return int(env_value(name, str(default)))
    except ValueError:
        return default


def one_line(value: str | None, limit: int = 90) -> str:
    if not value:
        return ""
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def media_summary(media: dict) -> str:
    parts = [
        f"id={media.get('id')}",
        f"type={media.get('media_type')}",
        f"product={media.get('media_product_type')}",
        f"comments_count={media.get('comments_count')}",
        f"like_count={media.get('like_count')}",
        f"timestamp={media.get('timestamp')}",
    ]
    permalink = media.get("permalink")
    if permalink:
        parts.append(f"permalink={permalink}")
    caption = one_line(media.get("caption"))
    if caption:
        parts.append(f"caption={caption}")
    return " | ".join(parts)


def main() -> int:
    load_dotenv()
    args = parse_args()

    access_token = env_value("INSTAGRAM_ACCESS_TOKEN", "")
    account_id = env_value("INSTAGRAM_USER_ID", "")
    api_version = env_value("INSTAGRAM_GRAPH_API_VERSION", "v25.0")
    database_path = env_value("DATABASE_PATH", "comments.db")
    media_id_filters = []
    if args.media_id.strip():
        media_id_filters = [args.media_id.strip()]
    else:
        media_id_filters = csv_env_values("INSTAGRAM_MEDIA_IDS")
    cache_minutes = env_int("WINGMAN_SYNC_CACHE_MINUTES", 10)
    sync_target = ",".join(sorted(media_id_filters)) if media_id_filters else "all"

    try:
        if not args.list_media:
            connection = connect(database_path)
            initialize_database(connection)

            if not args.override_cache and is_sync_cache_fresh(
                connection, "instagram", sync_target, cache_minutes
            ):
                print(
                    "Instagram sync cache is fresh "
                    f"({cache_minutes} minutes); skipping API sync."
                )
                connection.close()
                return 0

        client = InstagramClient(access_token, api_version)

        if not account_id:
            print("Discovering Instagram user_id from /me...")
        account_id = resolve_instagram_account_id(client, account_id)
        account = fetch_authenticated_account(client)
        account_username = (account.get("username") or "").casefold()
        if account_username:
            print(f"Detecting external replies from @{account.get('username')}")

        if args.list_media:
            media_count = 0
            for media in fetch_reels(client, account_id):
                if media_id_filters and media.get("id") not in media_id_filters:
                    continue
                media_count += 1
                print(media_summary(media))
                if args.media_id.strip():
                    break
            print(f"Found {media_count} matching Instagram Reels.")
            return 0

        fetched_count = 0
        media_count = 0
        reply_count = 0
        blank_count = 0

        reels = fetch_reels(client, account_id)
        for media in reels:
            if media_id_filters and media.get("id") not in media_id_filters:
                continue

            media_count += 1
            print(f"Syncing Reel {media_summary(media)}")

            media_comment_count = 0
            for comment in fetch_media_comments(client, media["id"]):
                media_comment_count += 1
                has_account_reply = False
                if not (comment.get("text") or "").strip():
                    blank_count += 1
                row = instagram_comment_row(comment, media)
                if not row.get("instagram_comment_id"):
                    print("Skipping an Instagram comment without an ID.")
                    continue

                upsert_instagram_comment(connection, row)
                fetched_count += 1

                for reply in fetch_comment_replies(client, comment["id"]):
                    if not (reply.get("text") or "").strip():
                        blank_count += 1
                    if (
                        account_username
                        and (reply.get("username") or "").casefold() == account_username
                    ):
                        has_account_reply = True
                    reply["parent_comment_id"] = comment.get("id")
                    reply_row = instagram_comment_row(reply, media, is_reply=True)
                    if not reply_row.get("instagram_comment_id"):
                        print("Skipping an Instagram reply without an ID.")
                        continue
                    upsert_instagram_comment(connection, reply_row)
                    fetched_count += 1
                    reply_count += 1

                reconcile_instagram_external_reply_status(
                    connection,
                    comment["id"],
                    has_account_reply,
                )

                if fetched_count % 100 == 0:
                    connection.commit()
                    print(
                        f"Fetched {fetched_count} Instagram comments "
                        f"({reply_count} replies)..."
                    )

            if media.get("comments_count", 0) and media_comment_count == 0:
                print(
                    "Warning: Instagram reports "
                    f"{media.get('comments_count')} comments for Reel {media.get('id')}, "
                    "but the comments endpoint returned no comment rows. Check that "
                    "the token includes instagram_business_manage_comments and that "
                    "the app has the required access level for this account."
                )

            if args.media_id.strip():
                break

        synced_at = mark_sync_completed(connection, "instagram", sync_target)
        connection.commit()
        total_in_database = count_instagram_comments(connection)
        connection.close()

        print(
            f"Instagram sync complete. Processed {fetched_count} API comment rows "
            f"from {media_count} Reels ({reply_count} replies, {blank_count} blank-text rows)."
        )
        print(f"Instagram sync cache updated at {synced_at}.")
        print(f"Instagram table now contains {total_in_database} unique comment rows.")
        return 0

    except InstagramApiError as exc:
        print(f"Instagram API error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nSync cancelled.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
