"""Focused tests for preference extraction and creator profile appends."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from learning_prompt import PREFERENCE_LEARNING_PROMPT
from learning_service import (
    ExtractedPreferences,
    PreferenceComparison,
    PreferenceLearningService,
    append_learned_preferences,
)


class FakeResponse:
    output_parsed = ExtractedPreferences(
        preferences=[
            "Fred prefers shorter replies.",
            "Fred acknowledges the idea before explaining.",
        ]
    )

    def model_dump_json(self, indent: int, warnings: bool = True) -> str:
        return '{"id":"learning-response"}'


class PreferenceLearningServiceTests(unittest.TestCase):
    def test_extracts_at_most_two_structured_preferences(self) -> None:
        parse = Mock(return_value=FakeResponse())
        client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
        service = PreferenceLearningService(client=client, model="test-model")

        preferences = service.extract_preferences(
            PreferenceComparison(
                original_draft="A long technical answer.",
                edited_reply="Good idea. Short answer.",
                comment_text="Could that work?",
                video_title="PID Explained",
            )
        )

        self.assertEqual(
            preferences,
            [
                "Fred prefers shorter replies.",
                "Fred acknowledges the idea before explaining.",
            ],
        )
        request = parse.call_args.kwargs
        self.assertEqual(request["model"], "test-model")
        self.assertEqual(request["text_format"], ExtractedPreferences)
        self.assertEqual(
            request["input"][0],
            {"role": "system", "content": PREFERENCE_LEARNING_PROMPT},
        )
        self.assertIn("Original AI draft", request["input"][1]["content"])
        self.assertIn("Creator's edited reply", request["input"][1]["content"])

    def test_appends_only_unique_preferences_under_new_section(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "creator.md"
            original = "# Fred\n\n## Style\n\n- Friendly.\n"
            profile.write_text(original, encoding="utf-8")

            first = append_learned_preferences(
                profile,
                [
                    "Fred prefers concise replies.",
                    "Fred acknowledges ideas before explaining.",
                ],
            )
            second = append_learned_preferences(
                profile,
                [
                    "Fred often prefers concise replies.",
                    "Fred uses humour for joke comments.",
                ],
            )
            updated = profile.read_text(encoding="utf-8")

        self.assertEqual(len(first.added), 2)
        self.assertEqual(
            second.added,
            ("Fred uses humour for joke comments.",),
        )
        self.assertEqual(
            second.skipped_duplicates,
            ("Fred often prefers concise replies.",),
        )
        self.assertTrue(updated.startswith(original.rstrip()))
        self.assertEqual(updated.count("## Learned Preferences"), 1)
        self.assertEqual(updated.count("prefers concise replies"), 1)
        self.assertIn("- Fred uses humour for joke comments.", updated)


if __name__ == "__main__":
    unittest.main()
