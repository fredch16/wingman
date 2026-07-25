"""Focused tests for structured OpenAI classification."""

import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import Mock

from classification_prompt import (
    CLASSIFICATION_PROMPT,
    PREVIOUS_CLASSIFICATION_PROMPT,
)
from classification_service import ClassificationService, CommentClassification


class FakeResponse:
    def __init__(self, classification: CommentClassification | None) -> None:
        self.output_parsed = classification

    def model_dump_json(self, indent: int, warnings: bool = True) -> str:
        return '{"id": "response-test"}'


class ClassificationServiceTests(unittest.TestCase):
    def test_prompt_balances_technical_and_community_value(self) -> None:
        self.assertIn(
            "How much would the creator regret overlooking this comment?",
            CLASSIFICATION_PROMPT,
        )
        self.assertIn("specific_appreciation", CLASSIFICATION_PROMPT)
        self.assertIn("humorous_engagement", CLASSIFICATION_PROMPT)
        self.assertIn("community_connection", CLASSIFICATION_PROMPT)
        self.assertIn("0.90-1.00", CLASSIFICATION_PROMPT)
        self.assertIn("0.00-0.09", CLASSIFICATION_PROMPT)
        self.assertIn("Priority is the most important output", CLASSIFICATION_PROMPT)
        self.assertIn("reliable immediate reply", CLASSIFICATION_PROMPT)
        self.assertIn("Use the supplied comment and video context", CLASSIFICATION_PROMPT)

    def test_classify_uses_responses_parse_and_returns_structured_result(self) -> None:
        expected = CommentClassification(
            category="technical_question",
            priority=0.9,
            reply_worthy=True,
            needs_research=False,
            reason="A specific technical question deserves an answer.",
        )
        parse = Mock(return_value=FakeResponse(expected))
        client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
        service = ClassificationService(client=client, model="test-model")

        with self.assertLogs("wingman.classification", level="INFO") as logs:
            result = service.classify(
                "How does derivative filtering work?",
                video_title="PID Explained",
            )

        self.assertEqual(result, expected)
        parse.assert_called_once_with(
            model="test-model",
            input=[
                {"role": "system", "content": CLASSIFICATION_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Comment:\nHow does derivative filtering work?"
                        "\n\nVideo:\nPID Explained"
                    ),
                },
            ],
            text_format=CommentClassification,
        )
        output = "\n".join(logs.output)
        self.assertIn("Classification prompt", output)
        self.assertIn("Raw response", output)
        self.assertIn("Parsed JSON", output)

    def test_classify_can_compare_with_previous_prompt(self) -> None:
        expected = CommentClassification(
            category="generic_praise",
            priority=0.3,
            reply_worthy=True,
            needs_research=False,
            reason="Genuine but low priority.",
        )
        parse = Mock(return_value=FakeResponse(expected))
        client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
        service = ClassificationService(client=client, model="test-model")

        service.classify("Nice video!", prompt=PREVIOUS_CLASSIFICATION_PROMPT)

        self.assertEqual(
            parse.call_args.kwargs["input"][0],
            {"role": "system", "content": PREVIOUS_CLASSIFICATION_PROMPT},
        )

    def test_classify_comment_reuses_the_same_classifier(self) -> None:
        @dataclass
        class StoredComment:
            text: str
            video_title: str

        expected = CommentClassification(
            category="technical_question",
            priority=0.8,
            reply_worthy=True,
            needs_research=False,
            reason="Useful question.",
        )
        parse = Mock(return_value=FakeResponse(expected))
        client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
        service = ClassificationService(client=client, model="test-model")

        result = service.classify_comment(
            StoredComment("Stored text", "Stored video")
        )

        self.assertEqual(result, expected)
        self.assertEqual(
            parse.call_args.kwargs["input"][1],
            {
                "role": "user",
                "content": "Comment:\nStored text\n\nVideo:\nStored video",
            },
        )

    def test_missing_parsed_output_is_an_error(self) -> None:
        client = SimpleNamespace(
            responses=SimpleNamespace(parse=Mock(return_value=FakeResponse(None)))
        )
        service = ClassificationService(client=client, model="test-model")

        with self.assertRaisesRegex(ValueError, "no parsed classification"):
            service.classify("Test comment")


if __name__ == "__main__":
    unittest.main()
