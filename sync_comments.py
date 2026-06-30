import os
import sys

from dotenv import load_dotenv

from database import connect, count_comments, initialize_database, upsert_comment
from youtube_api import (
    YouTubeApiError,
    authenticate,
    extract_top_level_comment,
    fetch_comment_threads,
    fetch_video_title,
    get_authenticated_channel_id,
)


def env_value(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def main() -> int:
    load_dotenv()

    client_secrets_file = env_value("YOUTUBE_CLIENT_SECRETS_FILE", "client_secret.json")
    token_file = env_value("YOUTUBE_TOKEN_FILE", "token.json")
    database_path = env_value("DATABASE_PATH", "comments.db")

    try:
        print("Authenticating with YouTube...")
        youtube = authenticate(client_secrets_file, token_file)

        print("Fetching authenticated channel ID...")
        channel_id = get_authenticated_channel_id(youtube)
        print(f"Authenticated channel: {channel_id}")

        connection = connect(database_path)
        initialize_database(connection)

        fetched_count = 0
        video_title_cache = {}

        for thread in fetch_comment_threads(youtube, channel_id):
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

            if fetched_count % 100 == 0:
                connection.commit()
                print(f"Fetched {fetched_count} comments...")

        connection.commit()
        total_in_database = count_comments(connection)
        connection.close()

        print(f"Sync complete. Fetched {fetched_count} comments this run.")
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
