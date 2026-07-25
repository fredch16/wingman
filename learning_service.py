"""Extract and safely append creator preferences learned from reply edits."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, Field

from learning_prompt import PREFERENCE_LEARNING_PROMPT

LOGGER = logging.getLogger("wingman.preference_learning")
LEARNED_PREFERENCES_HEADING = "## Learned Preferences"


class ExtractedPreferences(BaseModel):
    preferences: list[str] = Field(max_length=2)


@dataclass(frozen=True)
class PreferenceComparison:
    original_draft: str
    edited_reply: str
    comment_text: str
    video_title: str


@dataclass(frozen=True)
class AppendResult:
    added: tuple[str, ...]
    skipped_duplicates: tuple[str, ...]


class PreferenceLearningService:
    """Use one structured OpenAI request to identify durable edit preferences."""

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

    def extract_preferences(
        self, comparison: PreferenceComparison
    ) -> list[str]:
        prompt_input = [
            {"role": "system", "content": PREFERENCE_LEARNING_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Video:\n{comparison.video_title}\n\n"
                    f"Viewer comment:\n{comparison.comment_text}\n\n"
                    f"Original AI draft:\n{comparison.original_draft}\n\n"
                    f"Creator's edited reply:\n{comparison.edited_reply}"
                ),
            },
        ]
        LOGGER.info("Preference learning prompt: %s", prompt_input)
        response = self.client.responses.parse(
            model=self.model,
            input=prompt_input,
            text_format=ExtractedPreferences,
        )
        LOGGER.info(
            "Raw preference learning response: %s",
            response.model_dump_json(indent=2, warnings=False),
        )
        parsed = response.output_parsed
        if parsed is None:
            raise ValueError("OpenAI returned no parsed preferences.")
        preferences = clean_preferences(parsed.preferences)
        LOGGER.info("Parsed learned preferences: %s", preferences)
        return preferences[:2]


def clean_preferences(values: list[str] | tuple[str, ...]) -> list[str]:
    """Normalize review input while preserving human-readable prose."""
    cleaned: list[str] = []
    for value in values:
        preference = re.sub(r"^\s*[-*]\s*", "", value).strip()
        preference = re.sub(r"\s+", " ", preference)
        if preference and preference not in cleaned:
            cleaned.append(preference)
    return cleaned


def parse_review_preferences(value: str) -> list[str]:
    return clean_preferences(value.splitlines())[:2]


def learned_preferences(profile: str) -> list[str]:
    """Read bullets only from the learned section, never earlier sections."""
    marker = profile.find(LEARNED_PREFERENCES_HEADING)
    if marker < 0:
        return []
    section = profile[marker + len(LEARNED_PREFERENCES_HEADING) :]
    return clean_preferences(
        [
            line
            for line in section.splitlines()
            if re.match(r"^\s*[-*]\s+\S", line)
        ]
    )


def _comparison_key(preference: str) -> str:
    words = re.findall(r"[a-z0-9]+", preference.lower())
    return " ".join(word for word in words if word not in {"fred", "often"})


def preferences_are_similar(first: str, second: str) -> bool:
    """Catch exact and near-duplicate preferences without semantic guesswork."""
    first_key = _comparison_key(first)
    second_key = _comparison_key(second)
    if not first_key or not second_key:
        return first_key == second_key
    sequence_similarity = SequenceMatcher(None, first_key, second_key).ratio()
    first_words = set(first_key.split())
    second_words = set(second_key.split())
    overlap = len(first_words & second_words) / len(first_words | second_words)
    return sequence_similarity >= 0.82 or overlap >= 0.75


def append_learned_preferences(
    profile_path: str | Path,
    proposed_preferences: list[str],
) -> AppendResult:
    """Append unique preferences without modifying existing profile content."""
    path = Path(profile_path)
    if not path.is_file():
        raise ValueError(f"Creator profile not found: {path}")
    original = path.read_text(encoding="utf-8")
    existing = learned_preferences(original)
    added: list[str] = []
    skipped: list[str] = []

    for preference in clean_preferences(proposed_preferences):
        if any(
            preferences_are_similar(preference, known)
            for known in [*existing, *added]
        ):
            skipped.append(preference)
        else:
            added.append(preference)

    if added:
        updated = original.rstrip()
        if LEARNED_PREFERENCES_HEADING not in original:
            updated += f"\n\n{LEARNED_PREFERENCES_HEADING}"
        updated += "".join(f"\n\n- {preference}" for preference in added)
        path.write_text(updated + "\n", encoding="utf-8")
        for preference in added:
            LOGGER.info("Added learned creator preference: %s", preference)

    return AppendResult(tuple(added), tuple(skipped))
