"""Reporting agent services for Telegram control-plane commands."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.storage.models import ApprovalQueueItem, PublishAuditLog, WorkspaceDailyUsage


def _date_window_days(*, end_date: date, days: int) -> tuple[datetime, datetime]:
    end_dt = datetime(end_date.year, end_date.month, end_date.day, tzinfo=timezone.utc) + timedelta(days=1)
    start_dt = end_dt - timedelta(days=days)
    return start_dt, end_dt


def _usage_rows(
    session: Session,
    *,
    workspace_id: str,
    start_date: date,
    end_date: date,
) -> list[WorkspaceDailyUsage]:
    return list(
        session.scalars(
            select(WorkspaceDailyUsage).where(
                WorkspaceDailyUsage.workspace_id == workspace_id,
                WorkspaceDailyUsage.usage_date >= start_date,
                WorkspaceDailyUsage.usage_date <= end_date,
            )
        ).all()
    )


def _publish_rows(
    session: Session,
    *,
    workspace_id: str,
    start_at: datetime,
    end_at: datetime,
) -> list[PublishAuditLog]:
    return list(
        session.scalars(
            select(PublishAuditLog).where(
                PublishAuditLog.workspace_id == workspace_id,
                PublishAuditLog.created_at >= start_at,
                PublishAuditLog.created_at < end_at,
            )
        ).all()
    )


def _queue_rows(
    session: Session,
    *,
    workspace_id: str,
    start_at: datetime,
    end_at: datetime,
) -> list[ApprovalQueueItem]:
    return list(
        session.scalars(
            select(ApprovalQueueItem).where(
                ApprovalQueueItem.workspace_id == workspace_id,
                ApprovalQueueItem.created_at >= start_at,
                ApprovalQueueItem.created_at < end_at,
            )
        ).all()
    )


def _usage_summary(rows: list[WorkspaceDailyUsage]) -> Dict[str, int]:
    summary: Dict[str, int] = {}
    for row in rows:
        summary[row.action] = summary.get(row.action, 0) + int(row.count)
    return summary


def _publish_summary(rows: list[PublishAuditLog]) -> Dict[str, Any]:
    by_platform_status = Counter()
    errors: List[Dict[str, str]] = []
    for row in rows:
        by_platform_status[(row.platform, row.status)] += 1
        if row.status in {"failed", "blocked_plan", "blocked_cooldown"}:
            errors.append(
                {
                    "platform": row.platform,
                    "status": row.status,
                    "error": row.error_message or row.status,
                }
            )

    grouped: Dict[str, Dict[str, int]] = {}
    for (platform, status), count in by_platform_status.items():
        grouped.setdefault(platform, {})
        grouped[platform][status] = int(count)

    return {
        "by_platform": grouped,
        "errors": errors[:5],
    }


def _queue_summary(rows: list[ApprovalQueueItem]) -> Dict[str, int]:
    counter = Counter(row.status for row in rows)
    return {
        "pending": int(counter.get("pending", 0)),
        "approved": int(counter.get("approved", 0)),
        "published": int(counter.get("published", 0)),
        "failed": int(counter.get("failed", 0)),
        "rejected": int(counter.get("rejected", 0)),
    }


def _recommendations(usage: Dict[str, int], publish: Dict[str, Any], queue: Dict[str, int]) -> list[str]:
    recommendations: list[str] = []

    replies = int(usage.get("publish_reply", 0))
    if replies < 5:
        recommendations.append("Increase strategic replies volume to at least 5/day.")

    publish_by_platform = publish.get("by_platform", {})
    x_failed = int(publish_by_platform.get("x", {}).get("failed", 0))
    if x_failed > 0:
        recommendations.append("Review X OAuth/token health and recent publish errors.")

    blocked_plan = 0
    for platform_rows in publish_by_platform.values():
        blocked_plan += int(platform_rows.get("blocked_plan", 0))
    if blocked_plan > 0:
        recommendations.append("Plan limits are blocking output; adjust overrides or upgrade plan.")

    if int(queue.get("pending", 0)) > 10:
        recommendations.append("Approval queue is accumulating; increase approval cadence in Telegram.")

    if not recommendations:
        recommendations.append("Execution is stable; keep current cadence and monitor conversion signals.")

    return recommendations


def build_daily_report(
    session: Session,
    *,
    workspace_id: str,
    report_date: date | None = None,
) -> Dict[str, Any]:
    reference = report_date or datetime.now(timezone.utc).date()
    start_at, end_at = _date_window_days(end_date=reference, days=1)
    usage_rows = _usage_rows(session, workspace_id=workspace_id, start_date=reference, end_date=reference)
    publish_rows = _publish_rows(session, workspace_id=workspace_id, start_at=start_at, end_at=end_at)
    queue_rows = _queue_rows(session, workspace_id=workspace_id, start_at=start_at, end_at=end_at)

    usage = _usage_summary(usage_rows)
    publish = _publish_summary(publish_rows)
    queue = _queue_summary(queue_rows)
    recommendations = _recommendations(usage, publish, queue)

    return {
        "workspace_id": workspace_id,
        "period": "daily",
        "date": reference.isoformat(),
        "usage": usage,
        "publish": publish,
        "queue": queue,
        "recommendations": recommendations,
    }


def build_weekly_report(
    session: Session,
    *,
    workspace_id: str,
    end_date: date | None = None,
) -> Dict[str, Any]:
    reference_end = end_date or datetime.now(timezone.utc).date()
    start_date = reference_end - timedelta(days=6)
    start_at, end_at = _date_window_days(end_date=reference_end, days=7)

    usage_rows = _usage_rows(
        session,
        workspace_id=workspace_id,
        start_date=start_date,
        end_date=reference_end,
    )
    publish_rows = _publish_rows(session, workspace_id=workspace_id, start_at=start_at, end_at=end_at)
    queue_rows = _queue_rows(session, workspace_id=workspace_id, start_at=start_at, end_at=end_at)

    usage = _usage_summary(usage_rows)
    publish = _publish_summary(publish_rows)
    queue = _queue_summary(queue_rows)
    recommendations = _recommendations(usage, publish, queue)

    return {
        "workspace_id": workspace_id,
        "period": "weekly",
        "start_date": start_date.isoformat(),
        "end_date": reference_end.isoformat(),
        "usage": usage,
        "publish": publish,
        "queue": queue,
        "recommendations": recommendations,
    }
