"""Reviewable, deduplicated Instagram comment-to-DM delivery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from wingman.db.automation_repository import Automation, AutomationRepository
from wingman.instagram.client import InstagramClient


@dataclass(frozen=True)
class DeliverySummary:
    eligible: int = 0
    sent: int = 0
    skipped: int = 0
    failed: int = 0


def candidates(repo: AutomationRepository, automation: Automation) -> list[Any]:
    """Only comments created after the rule, within Meta's seven-day window."""
    return repo.connection.execute(
        """SELECT comment_id, video_id, text, author_channel_id, published_at
           FROM comments WHERE video_id = ? AND platform = 'instagram'
           AND published_at >= ? ORDER BY published_at, comment_id""",
        (automation.video_id, automation.created_at),
    ).fetchall()


def deliver_comment(
    repo: AutomationRepository,
    client: InstagramClient,
    account_id: str,
    automation: Automation,
    comment: Any,
) -> str:
    """Private opt-in first; claim before sending and never auto-retry uncertain sends."""
    if not repo.match_delivery(automation, comment["text"]):
        return "skipped"
    if repo.delivery(comment["comment_id"]):
        return "skipped"
    try:
        published = datetime.fromisoformat(comment["published_at"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return "skipped"
    age = datetime.now(timezone.utc) - published.astimezone(timezone.utc)
    if age.total_seconds() < 0 or age.total_seconds() >= 7 * 86400:
        return "skipped"
    sender_id = str(comment["author_channel_id"] or "")
    if not sender_id or sender_id == account_id:
        return "skipped"
    comment_id = str(comment["comment_id"])
    if not repo.claim_delivery(comment_id, automation.automation_id, sender_id):
        return "skipped"
    try:
        result = client.send_message(
            account_id,
            {"comment_id": comment_id},
            {
                "text": automation.initial_dm,
                "quick_replies": [{
                    "content_type": "text",
                    "title": "Yes please",
                    "payload": f"wingman_yes:{comment_id}",
                }],
            },
        )
        repo.update_delivery(
            comment_id, "awaiting_opt_in",
            recipient_id=str(result["recipient_id"]),
            private_message_id=str(result["message_id"]),
        )
    except Exception as error:
        repo.update_delivery(comment_id, "private_failed", error=str(error))
        return "failed"
    # Do not claim the resource was sent. The public acknowledgement says a DM is waiting.
    try:
        posted = client.reply(comment_id, automation.default_reply)
        repo.update_delivery(comment_id, "awaiting_opt_in", public_reply_id=posted.reply_id)
    except Exception as error:
        repo.update_delivery(comment_id, "public_failed", error=str(error))
        return "failed"
    return "sent"


def run_automation(
    repo: AutomationRepository,
    client: InstagramClient,
    account_id: str,
    automation: Automation,
) -> DeliverySummary:
    eligible = sent = skipped = failed = 0
    for comment in candidates(repo, automation):
        if not repo.match_delivery(automation, comment["text"]):
            continue
        eligible += 1
        outcome = deliver_comment(repo, client, account_id, automation, comment)
        sent += outcome == "sent"
        skipped += outcome == "skipped"
        failed += outcome == "failed"
    return DeliverySummary(eligible, sent, skipped, failed)


def handle_opt_in(
    repo: AutomationRepository,
    client: InstagramClient,
    account_id: str,
    sender_id: str,
    payload: str,
) -> bool:
    if not payload.startswith("wingman_yes:"):
        return False
    comment_id = payload.removeprefix("wingman_yes:")
    delivery = repo.delivery(comment_id)
    if not delivery or not repo.claim_followup(comment_id, sender_id):
        return False
    automation = repo.get(delivery["automation_id"])
    if automation is None:
        repo.update_delivery(comment_id, "followup_failed", error="Automation no longer exists.")
        return False
    try:
        result = client.send_message(
            account_id, {"id": sender_id}, {"text": automation.followup_dm}
        )
        repo.update_delivery(
            comment_id, "completed", followup_message_id=str(result["message_id"])
        )
        return True
    except Exception as error:
        repo.update_delivery(comment_id, "followup_failed", error=str(error))
        return False
