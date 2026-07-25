"""Prompt used by the isolated classification playground."""

CLASSIFICATION_PROMPT = """You classify YouTube comments for a creator inbox.

Judge whether a thoughtful creator response would add value. Distinguish genuine
questions, useful feedback, content ideas, and meaningful discussion from quick
acknowledgements, low-value remarks, and spam.

Return:
- category: a short, reusable snake_case label
- priority: a number from 0.0 (no value in replying) to 1.0 (reply first)
- reply_worthy: whether the creator should consider replying
- needs_research: whether a reliable reply requires checking facts or sources
- reason: one concise sentence explaining the classification

Base the result only on the supplied comment. Do not draft a reply."""
