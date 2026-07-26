"""Focused tests for preference extraction and creator profile appends."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from learning_prompt import (
    PREFERENCE_LEARNING_PROMPT,
    VIDEO_CONTEXT_LEARNING_PROMPT,
)
from learning_service import (
    ExtractedPreference,
    ExtractedPreferences,
    PreferenceComparison,
    PreferenceLearningService,
    VideoResponseComparison,
    VideoResponseGuidance,
    append_learned_preferences,
    format_video_response_guidance,
)


class FakeResponse:
    output_parsed = ExtractedPreferences(
        changes=[
            ExtractedPreference(
                change_type="length",
                preference="Fred prefers shorter replies.",
            ),
            ExtractedPreference(
                change_type="acknowledgement",
                preference="Fred acknowledges the idea before explaining.",
            ),
        ]
    )

    def model_dump_json(self, indent: int, warnings: bool = True) -> str:
        return '{"id":"learning-response"}'


class FakeVideoGuidanceResponse:
    output_parsed = VideoResponseGuidance(
        trigger="viewers ask what the button changes",
        model_answer="It changes the setpoint used by the controller.",
    )

    def model_dump_json(self, indent: int, warnings: bool = True) -> str:
        return '{"id":"video-learning-response"}'


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
                video_summary="A 60 second explanation of PID control.",
                category="technical_question",
                classification_reason="A useful technical question.",
            )
        )

        self.assertEqual(
            preferences,
            [
                ExtractedPreference(
                    change_type="length",
                    preference="Fred prefers shorter replies.",
                ),
                ExtractedPreference(
                    change_type="acknowledgement",
                    preference="Fred acknowledges the idea before explaining.",
                ),
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
        self.assertIn(
            "A 60 second explanation of PID control",
            request["input"][1]["content"],
        )
        self.assertIn("technical_question", request["input"][1]["content"])

    def test_extracts_video_specific_trigger_and_model_answer(self) -> None:
        parse = Mock(return_value=FakeVideoGuidanceResponse())
        client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
        service = PreferenceLearningService(client=client, model="test-model")

        guidance = service.extract_video_response_guidance(
            VideoResponseComparison(
                comment_text="What does the button do?",
                creator_reply="It changes the setpoint.",
                video_title="PID button demo",
                video_summary="A controller demonstration.",
            )
        )

        self.assertEqual(
            format_video_response_guidance(guidance),
            (
                "When viewers ask what the button changes, answer along these "
                "lines: It changes the setpoint used by the controller."
            ),
        )
        request = parse.call_args.kwargs
        self.assertEqual(request["text_format"], VideoResponseGuidance)
        self.assertEqual(
            request["input"][0],
            {"role": "system", "content": VIDEO_CONTEXT_LEARNING_PROMPT},
        )
        self.assertIn("What does the button do?", request["input"][1]["content"])
        self.assertIn("It changes the setpoint.", request["input"][1]["content"])

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
