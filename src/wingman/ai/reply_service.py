"""OpenAI reply generation with creator, video, comment, and thread context."""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from openai import OpenAI
from pydantic import BaseModel

from wingman.ai.reply_prompt import REPLY_GENERATION_PROMPT

LOGGER = logging.getLogger("wingman.reply_generation")


class ReplyableComment(Protocol):
    text: str
    video_title: str
    video_summary: str | None
    thread_id: str
    total_reply_count: int
    reply_direction: str | None
    draft_reply: str | None


@dataclass(frozen=True)
class ReplyContext:
    comment_text: str
    video_title: str
    video_summary: str | None = None
    thread_context: str | None = None
    creator_direction: str | None = None


class GeneratedReply(BaseModel):
    draft_reply: str


class ReplyGenerationService:
    def __init__(
        self,
        client: Any | None = None,
        model: str | None = None,
        creator_profile_path: str | Path | None = None,
    ) -> None:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if client is None and not api_key:
            raise ValueError("OPENAI_API_KEY is missing from the environment.")
        self.client = client or OpenAI(api_key=api_key)
        self.model = model or os.getenv("OPENAI_MODEL", "").strip() or "gpt-5.6-sol"
        configured_path = (
            creator_profile_path
            or os.getenv("CREATOR_CONTEXT_FILE", "").strip()
            or "creator.md"
        )
        self.creator_profile_path = Path(configured_path)

    def load_creator_profile(self) -> str:
        if not self.creator_profile_path.is_file():
            raise ValueError(
                f"Creator profile not found: {self.creator_profile_path}"
            )
        return self.creator_profile_path.read_text(encoding="utf-8").strip()

    def build_input(self, context: ReplyContext) -> list[dict[str, str]]:
        creator_profile = self.load_creator_profile()
        video_context = context.video_summary or "No manual video summary is available."
        thread_context = context.thread_context or "No additional thread context is available."
        creator_direction = (context.creator_direction or "").strip()
        direction_section = ""
        if creator_direction:
            direction_section = (
                "\n\nCreator's explicit reply direction:\n"
                "The creator explicitly asked for the reply to follow the "
                "direction below. Shape the reply around it while still "
                "answering the comment naturally. Treat it as guidance, not "
                "as text that must be copied verbatim.\n\n"
                f"{creator_direction}"
            )
        return [
            {"role": "system", "content": REPLY_GENERATION_PROMPT},
            {
                "role": "developer",
                "content": f"Creator profile:\n\n{creator_profile}",
            },
            {
                "role": "user",
                "content": (
                    f"Video title:\n{context.video_title}\n\n"
                    f"Video context:\n{video_context}\n\n"
                    f"Comment:\n{context.comment_text}\n\n"
                    f"Thread context:\n{thread_context}"
                    f"{direction_section}"
                ),
            },
        ]

    def generate_reply(self, context: ReplyContext) -> str:
        prompt_input = self.build_input(context)
        LOGGER.info("Reply prompt input: %s", prompt_input)
        response = self.client.responses.parse(
            model=self.model,
            input=prompt_input,
            text_format=GeneratedReply,
        )
        LOGGER.info(
            "Raw reply response: %s",
            response.model_dump_json(indent=2, warnings=False),
        )
        parsed = response.output_parsed
        if parsed is None:
            raise ValueError("OpenAI returned no parsed reply draft.")
        draft = parsed.draft_reply.strip()
        if not draft:
            raise ValueError("OpenAI returned an empty reply draft.")
        LOGGER.info("Parsed reply JSON: %s", parsed.model_dump_json(indent=2))
        return draft

    def generate_for_comment(
        self,
        comment: ReplyableComment,
        thread_context: str | None = None,
    ) -> str:
        available_thread_context = thread_context
        if available_thread_context is None and comment.total_reply_count:
            available_thread_context = (
                f"The stored top-level thread reports "
                f"{comment.total_reply_count} existing replies, but their text "
                "is not stored locally."
            )
        creator_direction = (
            getattr(comment, "reply_direction", None)
            or getattr(comment, "draft_reply", None)
        )
        return self.generate_reply(
            ReplyContext(
                comment_text=comment.text,
                video_title=comment.video_title,
                video_summary=comment.video_summary,
                thread_context=available_thread_context,
                creator_direction=creator_direction,
            )
        )
