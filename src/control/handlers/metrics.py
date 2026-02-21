"""Metrics command handler."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Dict

from sqlalchemy import select

from src.billing.plans import load_plans
from src.control.command_schema import ControlResponse
from src.control.services import get_or_create_control_setting, parse_channels
from src.storage.models import ApprovalQueueItem, DailyPostDraft, PublishAuditLog, Workspace, WorkspaceDailyUsage

if TYPE_CHECKING:
    from src.control.command_router import CommandContext


_ACTION_TO_LIMIT_KEY = {
    "publish_reply": "max_replies_per_day",
    "publish_post": "max_posts_per_day",
    "publish_email": "max_emails_per_day",
    "publish_blog": "max_blogs_per_day",
    "publish_instagram": "max_instagram_posts_per_day",
}


def _daily_usage_map(context: "CommandContext") -> Dict[str, int]:
    workspace_id = context.envelope.workspace_id
    today = datetime.now(timezone.utc).date()
    rows = context.session.scalars(
        select(WorkspaceDailyUsage).where(
            WorkspaceDailyUsage.workspace_id == workspace_id,
            WorkspaceDailyUsage.usage_date == today,
        )
    ).all()
    return {row.action: int(row.count) for row in rows}


def _plan_usage(context: "CommandContext", usage_map: Dict[str, int]) -> Dict[str, Dict[str, int]]:
    workspace_id = context.envelope.workspace_id
    workspace = context.session.scalar(select(Workspace).where(Workspace.id == workspace_id))
    plan = workspace.plan if workspace else "free"

    plans = load_plans()
    plan_limits = plans.get(plan, {})

    summary: Dict[str, Dict[str, int]] = {}
    for action, limit_key in _ACTION_TO_LIMIT_KEY.items():
        limit = int(plan_limits.get(limit_key, 0))
        used = int(usage_map.get(action, 0))
        summary[action] = {
            "limit": limit,
            "used": used,
            "remaining": max(limit - used, 0) if limit >= 0 else -1,
        }
    return summary


def handle(context: "CommandContext") -> ControlResponse:
    workspace_id = context.envelope.workspace_id
    now = datetime.now(timezone.utc)
    today = now.date()

    usage_map = _daily_usage_map(context)

    queue_items = context.session.scalars(
        select(ApprovalQueueItem).where(
            ApprovalQueueItem.workspace_id == workspace_id,
            ApprovalQueueItem.created_at >= datetime(today.year, today.month, today.day, tzinfo=timezone.utc),
        )
    ).all()

    queue_counter = Counter(item.status for item in queue_items)
    replies_generated = sum(1 for item in queue_items if item.item_type == "reply")

    publish_rows = context.session.scalars(
        select(PublishAuditLog).where(
            PublishAuditLog.workspace_id == workspace_id,
            PublishAuditLog.created_at >= datetime(today.year, today.month, today.day, tzinfo=timezone.utc),
        )
    ).all()

    blocked_counter = Counter()
    for row in publish_rows:
        if row.status == "blocked_plan":
            blocked_counter["plan_limit"] += 1
        elif row.status == "blocked_cooldown":
            blocked_counter["cooldown"] += 1
        elif row.status == "blocked_rate_limit":
            blocked_counter["hour_quota"] += 1
        elif row.status == "blocked_circuit_breaker":
            blocked_counter["circuit_breaker"] += 1

    latest_daily_post = context.session.scalar(
        select(DailyPostDraft)
        .where(DailyPostDraft.workspace_id == workspace_id)
        .order_by(DailyPostDraft.created_at.desc())
        .limit(1)
    )

    control = get_or_create_control_setting(context.session, workspace_id=workspace_id)

    data = {
        "workspace_id": workspace_id,
        "date": today.isoformat(),
        "replies_generated": replies_generated,
        "replies_published": int(usage_map.get("publish_reply", 0)),
        "reply_blocked": dict(blocked_counter),
        "daily_post": {
            "latest_status": latest_daily_post.status if latest_daily_post else "none",
            "published_today": int(usage_map.get("publish_post", 0)),
        },
        "email": {
            "published_today": int(usage_map.get("publish_email", 0)),
        },
        "blog": {
            "published_today": int(usage_map.get("publish_blog", 0)),
        },
        "instagram": {
            "published_today": int(usage_map.get("publish_instagram", 0)),
        },
        "queue": {
            "pending": int(queue_counter.get("pending", 0)),
            "approved": int(queue_counter.get("approved", 0)),
            "published": int(queue_counter.get("published", 0)),
            "failed": int(queue_counter.get("failed", 0)),
            "rejected": int(queue_counter.get("rejected", 0)),
        },
        "plan_usage": _plan_usage(context, usage_map),
        "channels": parse_channels(control),
    }

    return ControlResponse(success=True, message="metrics_ok", data=data)
