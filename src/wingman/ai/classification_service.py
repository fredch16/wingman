"""OpenAI-backed classification for the isolated prompt playground."""

import logging
import os
from typing import Any, Literal, Protocol

from openai import OpenAI
from pydantic import BaseModel, Field

from wingman.ai.classification_prompt import CLASSIFICATION_PROMPT

LOGGER = logging.getLogger("wingman.classification")
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False


class ClassifiableComment(Protocol):
    text: str
    video_title: str


class CommentClassification(BaseModel):
    category: Literal[
        "community_connection",
        "technical_question",
        "constructive_correction",
        "content_idea",
        "meaningful_discussion",
        "specific_appreciation",
        "humorous_engagement",
        "generic_praise",
        "spam",
        "low_value",
    ]
    priority: float = Field(ge=0.0, le=1.0)
    reply_worthy: bool
    needs_research: bool
    reason: str


class ClassificationService:
    def __init__(
        self,
        client: Any | None = None,
        model: str | None = None,
    ) -> None:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if client is None and not api_key:
            raise ValueError("OPENAI_API_KEY is missing from the environment.")
        self.client = client or OpenAI(api_key=api_key)
        self.model = model or os.getenv("OPENAI_MODEL", "").strip() or "gpt-5.6-sol"

    def classify(
        self,
        comment_text: str,
        prompt: str = CLASSIFICATION_PROMPT,
        video_title: str | None = None,
    ) -> CommentClassification:
        LOGGER.info("Classification prompt:\n%s", prompt)
        LOGGER.info("Comment to classify:\n%s", comment_text)
        LOGGER.info("Video context:\n%s", video_title or "Not provided")
        user_input = f"Comment:\n{comment_text}"
        if video_title:
            user_input += f"\n\nVideo:\n{video_title}"

        response = self.client.responses.parse(
            model=self.model,
            input=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_input},
            ],
            text_format=CommentClassification,
        )
        LOGGER.info(
            "Raw response:\n%s",
            response.model_dump_json(indent=2, warnings=False),
        )

        classification = response.output_parsed
        if classification is None:
            raise ValueError("OpenAI returned no parsed classification.")
        LOGGER.info(
            "Parsed JSON:\n%s", classification.model_dump_json(indent=2)
        )
        return classification

    def classify_comment(
        self, comment: ClassifiableComment
    ) -> CommentClassification:
        """Classify a stored comment through the same prompt and API path."""
        return self.classify(comment.text, video_title=comment.video_title)
