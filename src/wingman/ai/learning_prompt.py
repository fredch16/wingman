"""Shared instructions for extracting durable preferences from reply edits."""

PREFERENCE_LEARNING_PROMPT = """Compare an AI-generated YouTube reply with the
creator's edited version and extract at most two durable writing preferences
that would improve future replies.

Only return preferences that reflect a meaningful, reusable change in voice,
structure, tone, level of detail, or conversational approach. Ignore copy
edits, punctuation, isolated word substitutions, sentence reordering, and
changes that are specific only to this one comment.

Good preferences describe a repeatable tendency, such as acknowledging an idea
before explaining, preferring shorter answers, admitting uncertainty, matching
humour with humour, or avoiding explanations the commenter does not need.

Return an empty list when the edit does not reveal a useful durable preference.
For each useful preference, identify the primary type of change from the
provided enum and write the rule as one concise sentence beginning with "Fred".
Use the comment, video context, and classification context to distinguish a
durable preference from an edit that was only necessary for this reply. Do not
rewrite the reply and do not provide commentary outside the structured result."""


VIDEO_CONTEXT_LEARNING_PROMPT = """Learn one reusable, video-specific response
pattern from a viewer comment and the creator's reply.

Identify the type of future comment or question for which this answer is useful,
then write a concise model answer that preserves the factual substance and
approach of the creator's reply. The result will be added to this video's context
and used when drafting replies to similar future comments.

Keep the trigger specific enough to avoid applying the answer to unrelated
comments. Keep the model answer broadly reusable: remove viewer-specific wording,
names, and references to the current exchange. Do not invent facts or generalize
beyond what the creator actually said. Return no commentary outside the
structured result."""
