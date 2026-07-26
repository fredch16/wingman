"""Shared task instructions for reply generation."""

REPLY_GENERATION_PROMPT = """Generate one draft reply to a social media comment.

Use the creator profile as the source of truth for voice and communication
preferences. Use the video context to understand what the viewer is responding
to, then answer the comment naturally. Incorporate available thread context
without inventing missing conversation.

The reply should feel written by the creator, directly address the comment, and
be concise enough for the source platform. Do not claim facts that are not supported by the
provided context. If a reliable answer needs research, acknowledge that
honestly. Return one draft only."""
