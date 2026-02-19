"""Plan loading and plan-limit enforcement primitives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.config import get_settings
from src.storage.models import UsageLog, Workspace, WorkspaceControlSetting, WorkspaceDailyUsage


DEFAULT_ACTION_LIMIT_MAP = {
    "publish_reply": "max_replies_per_day",
    "publish_post": "max_posts_per_day",
    "publish_email": "max_emails_per_day",
    "publish_blog": "max_blogs_per_day",
}


@dataclass(frozen=True)
class PlanLimitDecision:
    allowed: bool
    workspace_id: str
    plan: str
    action: str
    limit_key: str
    limit: int
    used: int
    requested: int
    remaining: int


def _resolve_plan_path() -> Path:
    settings = get_settings()
    configured = Path(settings.plans_file_path)
    if configured.is_absolute():
        return configured
    return Path.cwd() / configured


@lru_cache(maxsize=1)
def load_plans() -> Dict[str, Dict[str, int]]:
    plan_path = _resolve_plan_path()
    with plan_path.open("r", encoding="utf-8") as file:
        content = yaml.safe_load(file) or {}
    if not isinstance(content, dict):
        raise ValueError("Invalid plans file format")

    plans: Dict[str, Dict[str, int]] = {}
    for plan_name, plan_limits in content.items():
        if not isinstance(plan_name, str) or not isinstance(plan_limits, dict):
            continue
        normalized_limits: Dict[str, int] = {}
        for key, value in plan_limits.items():
            if isinstance(key, str) and isinstance(value, int):
                normalized_limits[key] = value
        plans[plan_name] = normalized_limits
    return plans


def _resolve_limit_key(action: str) -> str:
    if action in DEFAULT_ACTION_LIMIT_MAP:
        return DEFAULT_ACTION_LIMIT_MAP[action]
    raise ValueError(f"Unsupported action for plan limit: {action}")


def _get_workspace(session: Session, workspace_id: str) -> Workspace:
    workspace = session.scalar(select(Workspace).where(Workspace.id == workspace_id))
    if workspace is None:
        raise LookupError("Workspace not found")
    return workspace


def _get_used_count(session: Session, workspace_id: str, action: str, usage_date: date) -> int:
    daily_usage = session.scalar(
        select(WorkspaceDailyUsage).where(
            WorkspaceDailyUsage.workspace_id == workspace_id,
            WorkspaceDailyUsage.action == action,
            WorkspaceDailyUsage.usage_date == usage_date,
        )
    )
    if daily_usage is None:
        return 0
    return int(daily_usage.count)


def _resolve_override_limit(
    session: Session,
    *,
    workspace_id: str,
    action: str,
    reference_time: datetime,
) -> Optional[int]:
    control = session.scalar(
        select(WorkspaceControlSetting).where(WorkspaceControlSetting.workspace_id == workspace_id)
    )
    if control is None:
        return None

    expires_at = control.limit_override_expires_at
    if expires_at is None:
        return None
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= reference_time:
        return None

    if action == "publish_reply":
        return control.reply_limit_override
    if action in {"publish_post", "publish_email", "publish_blog"}:
        return control.post_limit_override
    return None


def check_plan_limit(
    session: Session,
    *,
    workspace_id: str,
    action: str,
    requested: int = 1,
    usage_date: Optional[date] = None,
) -> PlanLimitDecision:
    if requested <= 0:
        raise ValueError("Requested amount must be positive")

    workspace = _get_workspace(session, workspace_id)
    plan_name = workspace.plan or "free"

    plans = load_plans()
    plan_limits = plans.get(plan_name)
    if plan_limits is None:
        raise ValueError(f"Plan is not configured: {plan_name}")

    now_utc = datetime.now(timezone.utc)
    override_limit = _resolve_override_limit(
        session,
        workspace_id=workspace_id,
        action=action,
        reference_time=now_utc,
    )

    limit_key = _resolve_limit_key(action)
    if override_limit is None:
        if limit_key not in plan_limits:
            raise ValueError(f"Limit key is not configured in plan '{plan_name}': {limit_key}")
        limit = int(plan_limits[limit_key])
    else:
        limit = int(override_limit)
        limit_key = f"{limit_key}_override"
        plan_name = f"{plan_name}:override"

    reference_date = usage_date or now_utc.date()
    used = _get_used_count(session, workspace_id, action, reference_date)

    if limit < 0:
        return PlanLimitDecision(
            allowed=True,
            workspace_id=workspace_id,
            plan=plan_name,
            action=action,
            limit_key=limit_key,
            limit=limit,
            used=used,
            requested=requested,
            remaining=-1,
        )

    remaining = max(limit - used, 0)
    allowed = used + requested <= limit
    final_remaining = max(limit - (used + requested), 0) if allowed else remaining
    return PlanLimitDecision(
        allowed=allowed,
        workspace_id=workspace_id,
        plan=plan_name,
        action=action,
        limit_key=limit_key,
        limit=limit,
        used=used,
        requested=requested,
        remaining=final_remaining,
    )


def record_usage(
    session: Session,
    *,
    workspace_id: str,
    action: str,
    amount: int = 1,
    occurred_at: Optional[datetime] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    if amount <= 0:
        raise ValueError("Usage amount must be positive")

    timestamp = occurred_at or datetime.now(timezone.utc)
    usage_day = timestamp.date()

    usage_log = UsageLog(
        workspace_id=workspace_id,
        action=action,
        count=amount,
        occurred_at=timestamp,
        payload_json=json_dumps(payload),
    )
    session.add(usage_log)

    aggregate = None
    for pending in session.new:
        if (
            isinstance(pending, WorkspaceDailyUsage)
            and pending.workspace_id == workspace_id
            and pending.action == action
            and pending.usage_date == usage_day
        ):
            aggregate = pending
            break

    if aggregate is None:
        aggregate = session.scalar(
            select(WorkspaceDailyUsage).where(
                WorkspaceDailyUsage.workspace_id == workspace_id,
                WorkspaceDailyUsage.action == action,
                WorkspaceDailyUsage.usage_date == usage_day,
            )
        )
    if aggregate is None:
        aggregate = WorkspaceDailyUsage(
            workspace_id=workspace_id,
            action=action,
            usage_date=usage_day,
            count=amount,
        )
        session.add(aggregate)
    else:
        aggregate.count = int(aggregate.count) + amount
        aggregate.updated_at = timestamp


def json_dumps(payload: Optional[Dict[str, Any]]) -> str:
    if payload is None:
        return "{}"
    import json

    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True)
