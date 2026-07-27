"""Synchronize every platform, then launch the Wingman inbox."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence

from wingman.instagram.sync import main as sync_instagram
from wingman.web import create_app
from wingman.youtube.sync import main as sync_youtube

SyncCommand = Callable[[list[str] | None], int]
AppLauncher = Callable[[str, int], None]


def launch_app(host: str, port: int) -> None:
    """Start Flask once, without a reloader that could repeat synchronization."""
    create_app().run(
        host=host,
        port=port,
        debug=False,
        use_reloader=False,
    )


def run_daily_workflow(
    *,
    refresh: bool = False,
    host: str = "127.0.0.1",
    port: int = 5000,
    youtube_command: SyncCommand = sync_youtube,
    instagram_command: SyncCommand = sync_instagram,
    app_launcher: AppLauncher = launch_app,
) -> int:
    """Run both synchronizers in order and launch only after successful syncs."""
    print("Daily sync 1/2: YouTube", flush=True)
    youtube_arguments = ["--refresh"] if refresh else []
    try:
        youtube_status = youtube_command(youtube_arguments)
    except Exception as error:
        print(f"YouTube synchronization failed: {error}", flush=True)
        return 1
    if youtube_status:
        print(
            "YouTube synchronization failed; the app was not started.",
            flush=True,
        )
        return youtube_status

    print("\nDaily sync 2/2: Instagram", flush=True)
    try:
        instagram_status = instagram_command([])
    except Exception as error:
        print(f"Instagram synchronization failed: {error}", flush=True)
        return 1
    if instagram_status:
        print(
            "Instagram synchronization failed; the app was not started.",
            flush=True,
        )
        return instagram_status

    print(
        f"\nAll platforms synchronized. Starting Wingman on "
        f"http://{host}:{port}",
        flush=True,
    )
    app_launcher(host, port)
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="force YouTube API checks even within the freshness window",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    return run_daily_workflow(
        refresh=args.refresh,
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    raise SystemExit(main())
