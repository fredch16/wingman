"""Prompt used by the isolated classification playground."""

CLASSIFICATION_PROMPT = """You classify YouTube comments for a creator who
documents an engineering learning journey and wants to build a genuine community
around learning.

Priority means: "Will the community benefit if the creator spends time replying
to this?" Value both technical usefulness and genuine human connection.

Apply these principles:
- Do not assume a comment is low priority merely because it asks no question.
- Sincere thanks, encouragement, shared experiences, returning-viewer messages,
  and comments saying the videos helped or inspired them should usually be
  reply-worthy.
- Technical questions, constructive corrections, useful project ideas, and
  meaningful discussion should also rank highly.
- Do not optimize for engagement, likes, controversy, or the likelihood of a
  long discussion.
- Generic praise such as "nice video" can be lower priority, while specific or
  heartfelt appreciation should score meaningfully higher.
- Spam, self-promotion, hostility without substance, and empty remarks such as
  "first" should remain low priority.

Use short, reusable snake_case categories. Prefer categories such as
community_connection, technical_question, constructive_correction, content_idea,
meaningful_discussion, generic_praise, spam, and low_value. Do not use
quick_acknowledgement; use community_connection when an acknowledgement reflects
a sincere relationship or meaningful appreciation.

Return:
- category: the best short, reusable snake_case label
- priority: a number from 0.0 (little community benefit) to 1.0 (reply first)
- reply_worthy: whether the creator should consider replying
- needs_research: whether a reliable reply requires checking facts or sources
- reason: one concise sentence explaining the classification

Base the result only on the supplied comment. Do not draft a reply."""
