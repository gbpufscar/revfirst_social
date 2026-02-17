"""Usage tracking service built on aggregated daily usage tables."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.billing.plans import PlanLimitDecision, check_plan_limit, record_usage
from src.storage.models import WorkspaceDailyUsage


class PlanLimitExceededError(RuntimeError):
    """Raised when a workspace exceeds the configured plan limit."""

    def __init__(self, decision: PlanLimitDecision):
        self.decision = decision
        super().__init__(
            f"Plan limit exceeded for action={decision.action} "
            f"(used={decision.used}, requested={decision.requested}, limit={decision.limit})"
        )


@dataclass(frozen=True)
class UsageConsumeResult:
    workspace_id: str
    action: str
    consumed: int
    used_before: int
    used_after: int
    limit: int
    remaining: int
    plan: str


def consume_workspace_action(
    session: Session,
    *,
    workspace_id: str,
    action: str,
    amount: int = 1,
    payload: Optional[Dict[str, Any]] = None,
    occurred_at: Optional[datetime] = None,
) -> UsageConsumeResult:
    """Check plan limit and persist usage log + daily aggregation atomically."""

    decision = check_plan_limit(
        session,
        workspace_id=workspace_id,
        action=action,
        requested=amount,
        usage_date=(occurred_at or datetime.now(timezone.utc)).date(),
    )
    if not decision.allowed:
        raise PlanLimitExceededError(decision)

    try:
        record_usage(
            session,
            workspace_id=workspace_id,
            action=action,
            amount=amount,
            occurred_at=occurred_at,
            payload=payload,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise

    return UsageConsumeResult(
        workspace_id=workspace_id,
        action=action,
        consumed=amount,
        used_before=decision.used,
        used_after=decision.used + amount,
        limit=decision.limit,
        remaining=decision.remaining,
        plan=decision.plan,
    )


def get_workspace_daily_usage(
    session: Session,
    *,
    workspace_id: str,
    usage_date: Optional[date] = None,
) -> List[WorkspaceDailyUsage]:
    """Read aggregated daily usage entries for a workspace."""

    reference_date = usage_date or datetime.now(timezone.utc).date()
    statement = (
        select(WorkspaceDailyUsage)
        .where(
            WorkspaceDailyUsage.workspace_id == workspace_id,
            WorkspaceDailyUsage.usage_date == reference_date,
        )
        .order_by(WorkspaceDailyUsage.action.asc())
    )
    return list(session.scalars(statement).all())

