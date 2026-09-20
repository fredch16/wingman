"""Read-only automation metrics derived from deduplicated delivery records."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from wingman.db.automation_repository import AutomationRepository
from wingman.db.comment_store import create_comments_table

FAILURE_STATUSES = {"failed", "private_failed", "public_failed", "followup_failed"}


@dataclass(frozen=True)
class AutomationMetrics:
    triggered: int = 0
    public_replies: int = 0
    followup_dms: int = 0
    failed: int = 0


@dataclass(frozen=True)
class PlatformMetrics:
    platform: str
    metrics: AutomationMetrics


@dataclass(frozen=True)
class VideoMetrics:
    video_id: str
    title: str
    platform: str
    thumbnail_url: str
    metrics: AutomationMetrics


@dataclass(frozen=True)
class DeliveryActivity:
    comment_id: str
    video_title: str
    platform: str
    status: str
    created_at: str
    error: str


@dataclass(frozen=True)
class AutomationAnalytics:
    total: AutomationMetrics
    platforms: tuple[PlatformMetrics, ...]
    videos: tuple[VideoMetrics, ...]
    recent: tuple[DeliveryActivity, ...]


def _metrics(rows: list[sqlite3.Row]) -> AutomationMetrics:
    return AutomationMetrics(
        triggered=len(rows),
        public_replies=sum(bool(row["public_reply_id"]) for row in rows),
        followup_dms=sum(bool(row["followup_message_id"]) for row in rows),
        failed=sum(row["status"] in FAILURE_STATUSES for row in rows),
    )


def get_automation_analytics(connection: sqlite3.Connection) -> AutomationAnalytics:
    """Count one trigger per comment, retaining history after rule deletion."""
    create_comments_table(connection)
    AutomationRepository(connection)
    rows = connection.execute(
        """SELECT d.comment_id, d.status, d.public_reply_id,
                  d.followup_message_id, d.error, d.created_at,
                  COALESCE(c.platform, v.platform, 'unknown') AS platform,
                  COALESCE(c.video_id, a.video_id, '') AS video_id,
                  COALESCE(v.title, c.video_id, a.video_id, 'Unknown video') AS video_title,
                  COALESCE(v.thumbnail_url, '') AS thumbnail_url
           FROM automation_deliveries d
           LEFT JOIN comments c ON c.comment_id = d.comment_id
           LEFT JOIN automations a ON a.automation_id = d.automation_id
           LEFT JOIN videos v ON v.video_id = COALESCE(c.video_id, a.video_id)
           ORDER BY d.created_at DESC, d.comment_id DESC"""
    ).fetchall()
    by_platform: dict[str, list[sqlite3.Row]] = {}
    by_video: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in rows:
        by_platform.setdefault(row["platform"], []).append(row)
        by_video.setdefault((row["platform"], row["video_id"]), []).append(row)
    platforms = tuple(
        PlatformMetrics(platform, _metrics(items))
        for platform, items in sorted(by_platform.items())
    )
    videos = tuple(sorted(
        (
            VideoMetrics(video_id, items[0]["video_title"], platform,
                         items[0]["thumbnail_url"], _metrics(items))
            for (platform, video_id), items in by_video.items()
        ),
        key=lambda item: (-item.metrics.triggered, item.title.casefold()),
    ))
    recent = tuple(
        DeliveryActivity(
            row["comment_id"], row["video_title"], row["platform"],
            row["status"], row["created_at"], row["error"],
        )
        for row in rows[:20]
    )
    return AutomationAnalytics(_metrics(rows), platforms, videos, recent)
