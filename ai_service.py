import os
from dataclasses import dataclass

from openai import OpenAI, OpenAIError


PROMPT_VERSION = "youtube_reply_v1"


class AIDraftError(RuntimeError):
    """Raised when Wingman cannot generate an AI draft."""


@dataclass
class DraftResult:
    text: str
    model: str
    provider: str
    prompt_version: str
    prompt_text: str


def env_value(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def configured_provider() -> str:
    return env_value("AI_PROVIDER", "openai").lower()


def configured_model() -> str:
    return env_value("OPENAI_MODEL", "gpt-5.5")


def generate_reply_draft(comment: dict) -> DraftResult:
    """Generate a reply draft for a YouTube comment."""
    provider = configured_provider()

    if provider != "openai":
        raise AIDraftError(f"Unsupported AI_PROVIDER: {provider}")

    return generate_openai_reply_draft(comment)


def generate_openai_reply_draft(comment: dict) -> DraftResult:
    api_key = env_value("OPENAI_API_KEY", "")
    model = configured_model()

    if not api_key:
        raise AIDraftError("OPENAI_API_KEY is missing from your environment.")

    client = OpenAI(api_key=api_key)

    prompt = build_reply_prompt(comment)

    try:
        response = client.responses.create(
            model=model,
            input=[
                {
                    "role": "developer",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "You draft concise YouTube creator replies. "
                                "Write in a natural, helpful tone. Do not invent "
                                "facts or make promises. If context is missing, "
                                "ask a brief clarifying question. Return only the "
                                "reply text, with no labels or quotation marks."
                            ),
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                },
            ],
        )
    except OpenAIError as exc:
        raise AIDraftError(f"OpenAI draft generation failed: {exc}") from exc

    draft_text = response.output_text.strip()
    if not draft_text:
        raise AIDraftError("OpenAI returned an empty draft.")

    return DraftResult(
        text=draft_text,
        model=model,
        provider="openai",
        prompt_version=PROMPT_VERSION,
        prompt_text=prompt,
    )


def build_reply_prompt(comment: dict) -> str:
    video_title = comment.get("video_title") or "Unknown video"
    author_name = comment.get("author_name") or "Unknown commenter"
    comment_text = comment.get("text") or ""
    notes = comment.get("notes") or ""

    return f"""
Video title:
{video_title}

Comment author:
{author_name}

Comment:
{comment_text}

Private notes from the creator:
{notes if notes else "None"}

Draft one short reply I can edit before posting. Keep it friendly and direct.
""".strip()
