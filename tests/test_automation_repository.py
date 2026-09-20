"""Focused tests for review-first keyword automations."""

import sqlite3
import unittest

from wingman.db.automation_repository import (
    AutomationRepository,
    normalize_keywords,
)
from wingman.db.video_catalog import Video, store_discovered_videos


class AutomationRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        store_discovered_videos(
            self.connection,
            [
                Video(
                    video_id="video-one",
                    title="Making a PCB",
                    published_at="2026-09-15T10:00:00Z",
                    thumbnail_url="https://example.test/pcb.jpg",
                ),
                Video(
                    video_id="video-two",
                    title="PID Explained",
                    published_at="2026-09-14T10:00:00Z",
                    thumbnail_url="",
                ),
            ],
        )
        self.repository = AutomationRepository(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def test_normalizes_comma_separated_variations_without_changing_case(self) -> None:
        self.assertEqual(
            normalize_keywords(" PCB, pcb, Pcb, PCB,  "),
            ("PCB", "pcb", "Pcb"),
        )

    def test_matches_case_sensitive_substrings_only_on_selected_video(self) -> None:
        automation_id = self.repository.create(
            "video-one",
            "PCB, pcb, Pcb",
            "Thanks, I’ll send you the link now.",
        )

        upper = self.repository.match("video-one", "Where can I get the PCB?")
        lower = self.repository.match("video-one", "Can I order this pcb?")

        assert upper is not None
        assert lower is not None
        self.assertEqual(upper.automation_id, automation_id)
        self.assertEqual(upper.matched_keyword, "PCB")
        self.assertEqual(lower.matched_keyword, "pcb")
        self.assertIsNone(
            self.repository.match("video-one", "Where is the PcB link?")
        )
        self.assertIsNone(
            self.repository.match("video-two", "Where can I get the PCB?")
        )

    def test_updates_pauses_and_deletes_automation(self) -> None:
        automation_id = self.repository.create(
            "video-one", "PCB", "Original response"
        )
        self.assertTrue(
            self.repository.update(
                automation_id,
                "board, Board",
                "Updated response",
            )
        )
        listed = self.repository.list_all()[0]
        self.assertEqual(listed.keywords, ("board", "Board"))
        self.assertEqual(listed.default_reply, "Updated response")
        self.assertEqual(listed.thumbnail_url, "https://example.test/pcb.jpg")

        self.assertTrue(self.repository.set_enabled(automation_id, False))
        self.assertIsNone(
            self.repository.match("video-one", "Which board did you use?")
        )
        self.assertTrue(self.repository.delete(automation_id))
        self.assertEqual(self.repository.list_all(), [])

    def test_migrates_existing_automations_without_losing_rule(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        store_discovered_videos(connection, [Video("older", "Older Reel", "2026-09-01T00:00:00Z", "", platform="instagram")])
        connection.execute("""CREATE TABLE automations (
            automation_id INTEGER PRIMARY KEY, video_id TEXT, keywords TEXT,
            default_reply TEXT, is_enabled INTEGER, created_at TEXT, updated_at TEXT,
            mode TEXT, initial_dm TEXT, followup_dm TEXT, match_type TEXT,
            public_reply_variants TEXT)""")
        connection.execute("""INSERT INTO automations VALUES
            (1, 'older', '[\"pcb\"]', 'Check DMs', 1, '2026-09-01', '2026-09-01',
             'instagram_dm', 'Want it?', 'Here it is', 'contains', '')""")
        try:
            rule = AutomationRepository(connection).get(1)
            self.assertEqual(rule.opt_in_button_label, "Yes please")
            self.assertEqual(rule.followup_links, ())
            self.assertEqual(rule.default_reply, "Check DMs")
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
