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
