import os
from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI, OpenAIError


PROMPT_VERSION = "youtube_reply_v4"
MAX_VIDEO_DESCRIPTION_CHARS = 1200


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


def configured_creator_context_file() -> str:
    return env_value("CREATOR_CONTEXT_FILE", "fred.md")


def load_creator_context() -> str:
    context_file = configured_creator_context_file()
    if not context_file:
        return ""

    path = Path(context_file)
    if not path.exists():
        return ""

    return path.read_text(encoding="utf-8").strip()


def generate_reply_draft(comment: dict) -> DraftResult:
    """Generate a reply draft for a YouTube comment."""
    provider = configured_provider()

    if provider != "openai":
        raise AIDraftError(f"Unsupported AI_PROVIDER: {provider}")

    return generate_openai_reply_draft(comment)


def refine_reply_draft(comment: dict, current_reply: str, hint: str) -> DraftResult:
    """Rewrite an existing reply draft using a short creator hint."""
    provider = configured_provider()

    if provider != "openai":
        raise AIDraftError(f"Unsupported AI_PROVIDER: {provider}")

    return generate_openai_reply_revision(comment, current_reply, hint)


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
                                "Follow the creator context and style rules in "
                                "the user prompt. Do not invent facts or make "
                                "promises. Return only the reply text, with no "
                                "labels or quotation marks."
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


def generate_openai_reply_revision(
    comment: dict, current_reply: str, hint: str
) -> DraftResult:
    api_key = env_value("OPENAI_API_KEY", "")
    model = configured_model()

    if not api_key:
        raise AIDraftError("OPENAI_API_KEY is missing from your environment.")

    current_reply = current_reply.strip()
    hint = hint.strip()
    if not current_reply:
        raise AIDraftError("There is no existing reply to rephrase.")
    if not hint:
        raise AIDraftError("Add a short nudge before rephrasing.")

    client = OpenAI(api_key=api_key)
    prompt = build_revision_prompt(comment, current_reply, hint)

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
                                "You revise concise creator replies. Preserve the "
                                "creator's intent, follow the requested nudge, and "
                                "return only the revised reply text."
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
        raise AIDraftError(f"OpenAI draft revision failed: {exc}") from exc

    draft_text = response.output_text.strip()
    if not draft_text:
        raise AIDraftError("OpenAI returned an empty revision.")

    return DraftResult(
        text=draft_text,
        model=model,
        provider="openai",
        prompt_version=f"{PROMPT_VERSION}_revision",
        prompt_text=prompt,
    )


def build_reply_prompt(comment: dict) -> str:
    video_title = comment.get("video_title") or "Unknown video"
    video_description = (comment.get("video_description") or "").strip()
    author_name = comment.get("author_name") or "Unknown commenter"
    comment_text = comment.get("text") or ""
    notes = (comment.get("notes") or "").strip()
    creator_context = load_creator_context()

    prompt_parts = []

    if creator_context:
        prompt_parts.append(
            f"""
Creator context:
{creator_context}
""".strip()
        )

    prompt_parts.append(
        f"""
Video title:
{video_title}

Comment author:
{author_name}

Comment:
{comment_text}
""".strip()
    )

    if video_description:
        if len(video_description) > MAX_VIDEO_DESCRIPTION_CHARS:
            video_description = (
                video_description[:MAX_VIDEO_DESCRIPTION_CHARS].rstrip() + "..."
            )
        prompt_parts.append(
            f"""
Manual video description:
{video_description}
""".strip()
        )

    if notes:
        prompt_parts.append(
            f"""
Extra creator notes:
{notes}
""".strip()
        )

    prompt_parts.append(
        """
Draft one short reply I can edit before posting. Keep it friendly and direct.
""".strip()
    )

    return "\n\n".join(prompt_parts)


def build_revision_prompt(comment: dict, current_reply: str, hint: str) -> str:
    base_prompt = build_reply_prompt(comment)
    return f"""
{base_prompt}

Existing draft:
{current_reply}

Revision nudge:
{hint}

Rewrite the existing draft instead of starting over. Keep it ready to post.
""".strip()
