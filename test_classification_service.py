"""Focused tests for structured OpenAI classification."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from classification_prompt import CLASSIFICATION_PROMPT
from classification_service import ClassificationService, CommentClassification


class FakeResponse:
    def __init__(self, classification: CommentClassification | None) -> None:
        self.output_parsed = classification

    def model_dump_json(self, indent: int) -> str:
        return '{"id": "response-test"}'


class ClassificationServiceTests(unittest.TestCase):
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
            result = service.classify("How does derivative filtering work?")

        self.assertEqual(result, expected)
        parse.assert_called_once_with(
            model="test-model",
            input=[
                {"role": "system", "content": CLASSIFICATION_PROMPT},
                {"role": "user", "content": "How does derivative filtering work?"},
            ],
            text_format=CommentClassification,
        )
        output = "\n".join(logs.output)
        self.assertIn("Classification prompt", output)
        self.assertIn("Raw response", output)
        self.assertIn("Parsed JSON", output)

    def test_missing_parsed_output_is_an_error(self) -> None:
        client = SimpleNamespace(
            responses=SimpleNamespace(parse=Mock(return_value=FakeResponse(None)))
        )
        service = ClassificationService(client=client, model="test-model")

        with self.assertRaisesRegex(ValueError, "no parsed classification"):
            service.classify("Test comment")


if __name__ == "__main__":
    unittest.main()
