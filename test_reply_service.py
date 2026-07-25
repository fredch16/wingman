"""Focused tests for layered reply prompt construction."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from reply_prompt import REPLY_GENERATION_PROMPT
from reply_service import GeneratedReply, ReplyContext, ReplyGenerationService


class FakeResponse:
    output_parsed = GeneratedReply(draft_reply="  A Fred-style draft.  ")

    def model_dump_json(self, indent: int, warnings: bool = True) -> str:
        return '{"id":"reply-response"}'


class ReplyGenerationServiceTests(unittest.TestCase):
    def test_builds_three_context_layers_and_returns_one_draft(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            creator_path = Path(directory) / "creator.md"
            creator_path.write_text("Fred is warm and technically honest.")
            parse = Mock(return_value=FakeResponse())
            client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
            service = ReplyGenerationService(
                client=client,
                model="test-model",
                creator_profile_path=creator_path,
            )

            draft = service.generate_reply(
                ReplyContext(
                    comment_text="Why use this filter?",
                    video_title="PID Explained",
                    video_summary="A short introduction to practical PID control.",
                    thread_context="Another viewer mentioned sensor noise.",
                )
            )

        self.assertEqual(draft, "A Fred-style draft.")
        request = parse.call_args.kwargs
        self.assertEqual(request["model"], "test-model")
        self.assertEqual(request["text_format"], GeneratedReply)
        self.assertEqual(
            request["input"][0],
            {"role": "system", "content": REPLY_GENERATION_PROMPT},
        )
        self.assertIn("Fred is warm", request["input"][1]["content"])
        self.assertIn("practical PID control", request["input"][2]["content"])
        self.assertIn("Why use this filter?", request["input"][2]["content"])
        self.assertIn("sensor noise", request["input"][2]["content"])

    def test_missing_creator_profile_is_clear_error(self) -> None:
        service = ReplyGenerationService(
            client=SimpleNamespace(),
            model="test-model",
            creator_profile_path="/missing/creator.md",
        )

        with self.assertRaisesRegex(ValueError, "Creator profile not found"):
            service.build_input(
                ReplyContext("Comment", "Video")
            )


if __name__ == "__main__":
    unittest.main()
