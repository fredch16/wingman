"""Tests for the all-platform daily workflow."""

import unittest

from wingman.daily import run_daily_workflow


class DailyWorkflowTests(unittest.TestCase):
    def test_syncs_both_platforms_before_launching_port_5000(self) -> None:
        events: list[object] = []

        def youtube(arguments: list[str] | None) -> int:
            events.append(("youtube", arguments))
            return 0

        def instagram(arguments: list[str] | None) -> int:
            events.append(("instagram", arguments))
            return 0

        def launch(host: str, port: int) -> None:
            events.append(("launch", host, port))

        status = run_daily_workflow(
            youtube_command=youtube,
            instagram_command=instagram,
            app_launcher=launch,
        )

        self.assertEqual(status, 0)
        self.assertEqual(
            events,
            [
                ("youtube", []),
                ("instagram", []),
                ("launch", "127.0.0.1", 5000),
            ],
        )

    def test_refresh_is_forwarded_to_youtube(self) -> None:
        arguments_seen: list[list[str] | None] = []

        status = run_daily_workflow(
            refresh=True,
            youtube_command=lambda arguments: (
                arguments_seen.append(arguments) or 0
            ),
            instagram_command=lambda _arguments: 0,
            app_launcher=lambda _host, _port: None,
        )

        self.assertEqual(status, 0)
        self.assertEqual(arguments_seen, [["--refresh"]])

    def test_youtube_failure_stops_before_instagram_and_app(self) -> None:
        events: list[str] = []

        status = run_daily_workflow(
            youtube_command=lambda _arguments: 2,
            instagram_command=lambda _arguments: events.append("instagram") or 0,
            app_launcher=lambda _host, _port: events.append("launch"),
        )

        self.assertEqual(status, 2)
        self.assertEqual(events, [])

    def test_instagram_failure_stops_before_app(self) -> None:
        events: list[str] = []

        status = run_daily_workflow(
            youtube_command=lambda _arguments: 0,
            instagram_command=lambda _arguments: 3,
            app_launcher=lambda _host, _port: events.append("launch"),
        )

        self.assertEqual(status, 3)
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
