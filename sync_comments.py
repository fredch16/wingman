import argparse
import os
import sys

from dotenv import load_dotenv

from database import (
    connect,
    count_comments,
    initialize_database,
    is_sync_cache_fresh,
    mark_sync_completed,
    reconcile_external_reply_status,
    upsert_comment,
)
from youtube_api import (
    YouTubeApiError,
    authenticate,
    extract_reply_comment,
    extract_top_level_comment,
    fetch_comment_replies,
    fetch_comment_threads,
    fetch_video_title,
    get_authenticated_channel_id,
    total_reply_count,
)


def env_value(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync YouTube comments and replies into SQLite."
    )
    parser.add_argument(
        "--video-id",
        default=env_value("YOUTUBE_VIDEO_ID", ""),
        help="Only sync comment threads and replies for this video ID.",
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


def main() -> int:
    load_dotenv()
    args = parse_args()

    client_secrets_file = env_value("YOUTUBE_CLIENT_SECRETS_FILE", "client_secret.json")
    token_file = env_value("YOUTUBE_TOKEN_FILE", "token.json")
    database_path = env_value("DATABASE_PATH", "comments.db")
    video_id_filter = args.video_id.strip()
    cache_minutes = env_int("WINGMAN_SYNC_CACHE_MINUTES", 10)
    sync_target = video_id_filter or "all"

    try:
        connection = connect(database_path)
        initialize_database(connection)

        if not args.override_cache and is_sync_cache_fresh(
            connection, "youtube", sync_target, cache_minutes
        ):
            print(
                "YouTube sync cache is fresh "
                f"({cache_minutes} minutes); skipping API sync."
            )
            connection.close()
            return 0

        print("Authenticating with YouTube...")
        youtube = authenticate(client_secrets_file, token_file)

        print("Fetching authenticated channel ID...")
        channel_id = get_authenticated_channel_id(youtube)
        print(f"Authenticated channel: {channel_id}")
        if video_id_filter:
            print(f"Syncing only video: {video_id_filter}")

        fetched_count = 0
        thread_count = 0
        reply_count = 0
        last_progress_count = 0
        video_title_cache = {}

        for thread in fetch_comment_threads(youtube, channel_id, video_id_filter):
            comment = extract_top_level_comment(thread)

            if not comment.get("youtube_comment_id"):
                print("Skipping a comment thread without a top-level comment ID.")
                continue

            video_id = comment.get("video_id")
            if video_id:
                if video_id not in video_title_cache:
                    video_title_cache[video_id] = fetch_video_title(youtube, video_id)
                comment["video_title"] = video_title_cache[video_id]

            upsert_comment(connection, comment)
            fetched_count += 1
            thread_count += 1
            has_channel_reply = False

            if total_reply_count(thread):
                for reply in fetch_comment_replies(youtube, comment["youtube_comment_id"]):
                    reply_comment = extract_reply_comment(reply, thread)
                    reply_comment["video_title"] = comment.get("video_title")

                    if not reply_comment.get("youtube_comment_id"):
                        print("Skipping a reply without a comment ID.")
                        continue

                    upsert_comment(connection, reply_comment)
                    if reply_comment.get("author_channel_id") == channel_id:
                        has_channel_reply = True
                    fetched_count += 1
                    reply_count += 1

                    if (
                        fetched_count % 100 == 0
                        and fetched_count != last_progress_count
                    ):
                        connection.commit()
                        print(
                            f"Fetched {fetched_count} comments "
                            f"({thread_count} top-level, {reply_count} replies)..."
                        )
                        last_progress_count = fetched_count

            reconcile_external_reply_status(
                connection,
                comment["youtube_comment_id"],
                has_channel_reply,
            )

            if fetched_count % 100 == 0 and fetched_count != last_progress_count:
                connection.commit()
                print(
                    f"Fetched {fetched_count} comments "
                    f"({thread_count} top-level, {reply_count} replies)..."
                )
                last_progress_count = fetched_count

        synced_at = mark_sync_completed(connection, "youtube", sync_target)
        connection.commit()
        total_in_database = count_comments(connection)
        connection.close()

        print(
            f"Sync complete. Fetched {fetched_count} comments this run "
            f"({thread_count} top-level, {reply_count} replies)."
        )
        print(f"YouTube sync cache updated at {synced_at}.")
        print(f"Database now contains {total_in_database} comments.")
        return 0

    except FileNotFoundError as exc:
        print(f"Missing credentials: {exc}", file=sys.stderr)
        print("Copy .env.example to .env and set YOUTUBE_CLIENT_SECRETS_FILE.", file=sys.stderr)
        return 1
    except YouTubeApiError as exc:
        print(f"YouTube API error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nSync cancelled.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
