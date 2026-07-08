import argparse
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync configured comment sources, then start Wingman."
    )
    parser.add_argument(
        "--skip-youtube",
        action="store_true",
        help="Do not run the YouTube comment sync.",
    )
    parser.add_argument(
        "--skip-instagram",
        action="store_true",
        help="Do not run the Instagram comment sync.",
    )
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Start the app without running any sync scripts.",
    )
    parser.add_argument(
        "--override-cache",
        action="store_true",
        help="Force sync scripts to call APIs even when the sync cache is fresh.",
    )
    parser.add_argument(
        "--youtube-video-id",
        default="",
        help="Only sync this YouTube video ID.",
    )
    parser.add_argument(
        "--instagram-media-id",
        default="",
        help="Only sync this Instagram media/Reel ID.",
    )
    parser.add_argument(
        "--port",
        default="",
        help="Override WINGMAN_PORT for this run.",
    )
    return parser.parse_args()


def run_step(label: str, command: list[str], env: dict[str, str]) -> None:
    print(f"\n==> {label}")
    result = subprocess.run(command, cwd=ROOT, env=env)
    if result.returncode:
        raise SystemExit(result.returncode)


def main() -> int:
    load_dotenv(ROOT / ".env")
    args = parse_args()

    env = os.environ.copy()
    if args.port:
        env["WINGMAN_PORT"] = args.port

    python = sys.executable

    if not args.no_sync and not args.skip_youtube:
        command = [python, "sync_comments.py"]
        if args.youtube_video_id:
            command.extend(["--video-id", args.youtube_video_id])
        if args.override_cache:
            command.append("--override-cache")
        run_step("Syncing YouTube comments", command, env)

    if not args.no_sync and not args.skip_instagram:
        command = [python, "sync_instagram_comments.py"]
        if args.instagram_media_id:
            command.extend(["--media-id", args.instagram_media_id])
        if args.override_cache:
            command.append("--override-cache")
        run_step("Syncing Instagram comments", command, env)

    print("\n==> Starting Wingman")
    try:
        return subprocess.call([python, "app.py"], cwd=ROOT, env=env)
    except KeyboardInterrupt:
        print("\nWingman stopped.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
