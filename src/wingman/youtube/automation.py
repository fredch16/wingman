"""Opt-in, deduplicated keyword replies for newly received YouTube comments."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from wingman.db.automation_repository import AutomationRepository
from wingman.db.inbox_repository import CommentRepository
from wingman.youtube.detection import get_stored_creator_channel_id
from wingman.youtube.reply import post_comment_reply
from wingman.youtube.sync import Comment


@dataclass(frozen=True)
class AutoReplySummary:
    matched: int = 0
    sent: int = 0
    skipped: int = 0
    failed: int = 0


def _timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def run_youtube_auto_replies(
    youtube: Any, connection: sqlite3.Connection, comments: list[Comment]
) -> AutoReplySummary:
    """Only post for enabled rules and comments newer than the rule itself."""
    automations = AutomationRepository(connection)
    rules_by_video: dict[str, list[Any]] = {}
    for rule in sorted(automations.list_all(), key=lambda item: item.automation_id):
        if rule.mode == "youtube_reply" and rule.is_enabled and rule.platform == "youtube":
            rules_by_video.setdefault(rule.video_id, []).append(rule)
    if not rules_by_video:
        return AutoReplySummary()

    creator_id = get_stored_creator_channel_id(connection)
    inbox = CommentRepository(connection)
    matched = sent = skipped = failed = 0
    for comment in comments:
        for rule in rules_by_video.get(comment.video_id, []):
            if not automations.match_delivery(rule, comment.text):
                continue
            matched += 1
            # Editing or re-enabling a rule must not retroactively publish to
            # comments received while it was paused or in prefill mode.
            published, created = _timestamp(comment.published_at), _timestamp(rule.updated_at)
            stored = inbox.get_comment(comment.comment_id)
            pending = connection.execute(
                "SELECT status FROM reply_queue WHERE comment_id = ?", (comment.comment_id,)
            ).fetchone() if _reply_queue_exists(connection) else None
            if (
                published is None or created is None or published < created
                or (creator_id and comment.author_channel_id == creator_id)
                or comment.author_display_name.casefold() == "@charbonnierlabs"
                or not comment.can_reply or not comment.is_public
                or stored is None or stored.status != "new" or stored.has_creator_reply
                or bool(stored.draft_reply or stored.final_reply)
                or stored.is_ignored or comment.total_reply_count > 0
                or (pending and pending["status"] in {"queued", "sending", "sent"})
                or automations.delivery(comment.comment_id)
            ):
                skipped += 1
                break
            if not automations.claim_delivery(
                comment.comment_id, rule.automation_id, comment.author_channel_id or ""
            ):
                skipped += 1
                break
            options = (rule.default_reply, *rule.public_reply_variants)
            text = options[(automations.delivery_count(rule.automation_id) - 1) % len(options)]
            try:
                posted = post_comment_reply(youtube, comment.comment_id, text)
                inbox.mark_platform_replied(
                    comment.comment_id, posted.reply_id, posted.text, posted.published_at
                )
                automations.update_delivery(
                    comment.comment_id, "completed", public_reply_id=posted.reply_id
                )
                print(f"YouTube auto-reply sent for {comment.comment_id}.")
                sent += 1
            except Exception as error:
                # The platform may have accepted a request that timed out.
                # Preserve the claim and require manual review before any retry.
                automations.update_delivery(comment.comment_id, "failed", error=str(error))
                print(f"YouTube auto-reply failed for {comment.comment_id}: {error}")
                failed += 1
            break
    return AutoReplySummary(matched, sent, skipped, failed)


def _reply_queue_exists(connection: sqlite3.Connection) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'reply_queue'"
    ).fetchone() is not None
