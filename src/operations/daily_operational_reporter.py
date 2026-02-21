"""Deterministic daily operational reporting for Telegram."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any, Dict, List

from redis import Redis
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from src.control.security import load_admin_directory
from src.control.services import DEFAULT_OPERATIONAL_MODE, editorial_stock_snapshot, normalize_operational_mode
from src.core.logger import get_logger
from src.storage.models import PublishAuditLog, Role, WorkspaceControlSetting, WorkspaceEvent, WorkspaceUser
from src.storage.redis_client import get_client as get_redis_client

logger = get_logger("revfirst.operations.daily_operational_reporter")

_PUBLISH_ACTIONS = {
    "publish_reply",
    "publish_post",
    "publish_email",
    "publish_blog",
    "publish_instagram",
}


def _normalize_dt(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _safe_json_dict(value: str | None) -> Dict[str, Any]:
    if not value:
        return {}
    try:
        payload = json.loads(value)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _window_start(now: datetime) -> datetime:
    return now - timedelta(hours=24)


def _format_hhmm_utc(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "n/d"
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return "n/d"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    return parsed.strftime("%H:%M UTC")


def classify_risk_level(
    *,
    stability_critical_count: int,
    auto_containments: int,
    kill_switch_activations: int,
    rate_limit_blocks: int,
    consecutive_failure_triggers: int,
    success_rate_pct: int,
) -> str:
    if kill_switch_activations > 0 or stability_critical_count > 0:
        return "CRITICAL"
    if auto_containments > 0 or rate_limit_blocks > 0 or consecutive_failure_triggers > 0:
        return "HIGH"
    if success_rate_pct < 80:
        return "MEDIUM"
    return "LOW"


def _resolve_workspace_mode(session: Session, *, workspace_id: str) -> str:
    raw_mode = session.scalar(
        select(WorkspaceControlSetting.operational_mode)
        .where(WorkspaceControlSetting.workspace_id == workspace_id)
        .limit(1)
    )
    return normalize_operational_mode(str(raw_mode or DEFAULT_OPERATIONAL_MODE))


def _stability_summary(session: Session, *, workspace_id: str, since: datetime) -> Dict[str, int]:
    rows = list(
        session.scalars(
            select(WorkspaceEvent)
            .where(
                WorkspaceEvent.workspace_id == workspace_id,
                WorkspaceEvent.created_at >= since,
                WorkspaceEvent.event_type.in_(
                    [
                        "stability_guard_report_generated",
                        "stability_guard_containment",
                        "stability_guard_kill_switch",
                    ]
                ),
            )
            .order_by(desc(WorkspaceEvent.created_at))
        ).all()
    )

    report_count = 0
    critical_count = 0
    high_count = 0
    auto_containment_count = 0
    kill_switch_count = 0

    for row in rows:
        payload = _safe_json_dict(row.payload_json)
        if row.event_type == "stability_guard_report_generated":
            report_count += 1
            overall_status = str(payload.get("overall_status") or "").strip().lower()
            if overall_status == "critical":
                critical_count += 1
            elif overall_status == "warning":
                high_count += 1
            continue

        if row.event_type == "stability_guard_containment":
            actions = payload.get("actions_applied")
            trigger = str(payload.get("trigger") or "").strip().lower()
            has_actions = isinstance(actions, list) and len(actions) > 0
            if has_actions and trigger != "manual":
                auto_containment_count += 1
            continue

        if row.event_type == "stability_guard_kill_switch":
            kill_switch_count += 1

    return {
        "reports": report_count,
        "critical": critical_count,
        "high": high_count,
        "auto_containments": auto_containment_count,
        "kill_switch": kill_switch_count,
    }


def _publishing_summary(session: Session, *, workspace_id: str, since: datetime) -> Dict[str, int]:
    rows = list(
        session.scalars(
            select(PublishAuditLog)
            .where(
                PublishAuditLog.workspace_id == workspace_id,
                PublishAuditLog.created_at >= since,
            )
            .order_by(desc(PublishAuditLog.created_at))
        ).all()
    )

    filtered = [row for row in rows if row.action in _PUBLISH_ACTIONS]
    attempts = len(filtered)
    success = sum(1 for row in filtered if row.status == "published")
    failures = sum(1 for row in filtered if row.status == "failed")
    success_rate_pct = int(round((success / attempts) * 100)) if attempts > 0 else 0
    rate_limit_blocks = sum(1 for row in filtered if row.status == "blocked_rate_limit")
    x_http_429 = sum(
        1
        for row in filtered
        if row.platform == "x"
        and "429" in str(row.error_message or "")
    )

    return {
        "attempts": attempts,
        "success": success,
        "failures": failures,
        "success_rate_pct": success_rate_pct,
        "rate_limit_blocks": rate_limit_blocks,
        "x_http_429": x_http_429,
    }


def _consecutive_failure_triggers(session: Session, *, workspace_id: str, since: datetime) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(WorkspaceEvent)
            .where(
                WorkspaceEvent.workspace_id == workspace_id,
                WorkspaceEvent.created_at >= since,
                WorkspaceEvent.event_type == "publishing_circuit_breaker_triggered",
            )
        )
        or 0
    )


def _redis_snapshot(*, workspace_id: str, redis_client: Redis | None) -> Dict[str, int]:
    if redis_client is None:
        return {"active_locks": 0, "ttl_anomalies": 0}

    lock_keys: set[str] = set()
    scheduler_lock_key = f"revfirst:{workspace_id}:scheduler:lock"
    run_lock_pattern = f"revfirst:{workspace_id}:control:run:*:lock"
    refresh_lock_key = f"revfirst:{workspace_id}:integrations:x:refresh_lock"

    try:
        if redis_client.exists(scheduler_lock_key):
            lock_keys.add(scheduler_lock_key)
    except Exception:
        return {"active_locks": 0, "ttl_anomalies": 0}

    for pattern in [run_lock_pattern]:
        try:
            keys = redis_client.keys(pattern)
        except Exception:
            keys = []
        for key in keys:
            lock_keys.add(str(key))

    try:
        if redis_client.exists(refresh_lock_key):
            lock_keys.add(refresh_lock_key)
    except Exception:
        pass

    ttl_anomalies = 0
    ttl_fn = getattr(redis_client, "ttl", None)
    if callable(ttl_fn):
        for key in lock_keys:
            try:
                ttl = int(ttl_fn(key))
            except Exception:
                continue
            if ttl <= 0:
                ttl_anomalies += 1

    return {"active_locks": len(lock_keys), "ttl_anomalies": ttl_anomalies}


def _send_via_control_telegram_service(*, chat_id: str, text: str) -> None:
    # Lazy import avoids control/orchestrator circular imports at module load.
    from src.control.telegram_bot import _send_telegram_chat_message

    _send_telegram_chat_message(chat_id=chat_id, text=text)


def _owner_admin_chat_ids(session: Session, *, workspace_id: str) -> List[str]:
    directory = load_admin_directory()
    allowed_ids = directory.allowed_telegram_ids
    if not allowed_ids:
        return []

    candidate_bindings = {
        chat_id: binding
        for chat_id, binding in directory.bindings.items()
        if chat_id in allowed_ids
    }
    if not candidate_bindings:
        return []

    user_ids = [binding.user_id for binding in candidate_bindings.values()]
    rows = session.execute(
        select(WorkspaceUser.user_id, Role.name)
        .join(Role, WorkspaceUser.role_id == Role.id)
        .where(
            WorkspaceUser.workspace_id == workspace_id,
            WorkspaceUser.user_id.in_(user_ids),
        )
    ).all()
    role_by_user_id = {str(user_id): str(role).strip().lower() for user_id, role in rows}

    recipients: List[str] = []
    for chat_id, binding in candidate_bindings.items():
        workspace_role = role_by_user_id.get(binding.user_id, "")
        if workspace_role in {"owner", "admin"}:
            recipients.append(chat_id)
    return sorted(set(recipients))


def build_daily_operational_snapshot(
    session: Session,
    *,
    workspace_id: str,
    redis_client: Redis | None = None,
    now: datetime | None = None,
) -> Dict[str, Any]:
    now_utc = _normalize_dt(now or datetime.now(timezone.utc))
    since = _window_start(now_utc)
    mode = _resolve_workspace_mode(session, workspace_id=workspace_id)

    stability = _stability_summary(session, workspace_id=workspace_id, since=since)
    publishing = _publishing_summary(session, workspace_id=workspace_id, since=since)
    breakers = {
        "rate_limit_blocks": publishing["rate_limit_blocks"],
        "consecutive_failure_triggers": _consecutive_failure_triggers(
            session,
            workspace_id=workspace_id,
            since=since,
        ),
    }
    redis_snapshot = _redis_snapshot(workspace_id=workspace_id, redis_client=redis_client)
    editorial_stock = editorial_stock_snapshot(session, workspace_id=workspace_id, now_utc=now_utc)
    risk = classify_risk_level(
        stability_critical_count=stability["critical"],
        auto_containments=stability["auto_containments"],
        kill_switch_activations=stability["kill_switch"],
        rate_limit_blocks=breakers["rate_limit_blocks"],
        consecutive_failure_triggers=breakers["consecutive_failure_triggers"],
        success_rate_pct=publishing["success_rate_pct"],
    )

    return {
        "workspace_id": workspace_id,
        "date_utc": now_utc.date().isoformat(),
        "mode": mode,
        "editorial_stock": editorial_stock,
        "stability": stability,
        "publishing": {
            "attempts": publishing["attempts"],
            "success": publishing["success"],
            "failures": publishing["failures"],
            "success_rate_pct": publishing["success_rate_pct"],
        },
        "circuit_breakers": breakers,
        "x_rate_limits": {"http_429_count": publishing["x_http_429"]},
        "redis": redis_snapshot,
        "risk_assessment": risk,
    }


def format_daily_operational_report(snapshot: Dict[str, Any]) -> str:
    date_utc = str(snapshot.get("date_utc") or "n/d")
    mode = str(snapshot.get("mode") or "semi_autonomous")
    editorial = snapshot.get("editorial_stock") if isinstance(snapshot.get("editorial_stock"), dict) else {}
    stability = snapshot.get("stability") if isinstance(snapshot.get("stability"), dict) else {}
    publishing = snapshot.get("publishing") if isinstance(snapshot.get("publishing"), dict) else {}
    circuit = snapshot.get("circuit_breakers") if isinstance(snapshot.get("circuit_breakers"), dict) else {}
    x_rate = snapshot.get("x_rate_limits") if isinstance(snapshot.get("x_rate_limits"), dict) else {}
    redis = snapshot.get("redis") if isinstance(snapshot.get("redis"), dict) else {}
    risk = str(snapshot.get("risk_assessment") or "LOW")
    next_window = _format_hhmm_utc(editorial.get("next_window_utc"))
    coverage = editorial.get("coverage_days") if editorial.get("coverage_days") is not None else "0.0"

    return (
        "📊 DAILY OPERATIONAL REPORT\n"
        "----------------------------\n"
        "\n"
        "Date:\n"
        f"{date_utc} (UTC)\n"
        "\n"
        "Mode:\n"
        f"{mode}\n"
        "\n"
        "Publishing:\n"
        f"Attempts: {int(publishing.get('attempts') or 0)}\n"
        f"Success: {int(publishing.get('success') or 0)}\n"
        f"Failures: {int(publishing.get('failures') or 0)}\n"
        f"Success Rate: {int(publishing.get('success_rate_pct') or 0)}%\n"
        "\n"
        "Editorial Stock:\n"
        f"Pending Review: {int(editorial.get('pending_review_count') or 0)}\n"
        f"Approved Scheduled: {int(editorial.get('approved_scheduled_count') or 0)}\n"
        f"Next Window: {next_window}\n"
        f"Coverage: {coverage} days\n"
        "\n"
        "Stability:\n"
        f"Reports: {int(stability.get('reports') or 0)}\n"
        f"Critical: {int(stability.get('critical') or 0)}\n"
        f"High: {int(stability.get('high') or 0)}\n"
        f"Containments: {int(stability.get('auto_containments') or 0)}\n"
        f"Kill-Switch: {int(stability.get('kill_switch') or 0)}\n"
        "\n"
        "Circuit Breakers:\n"
        f"Rate Limit Blocks: {int(circuit.get('rate_limit_blocks') or 0)}\n"
        f"Consecutive Failure Triggers: {int(circuit.get('consecutive_failure_triggers') or 0)}\n"
        "\n"
        "Rate Limits (X):\n"
        f"429 Responses: {int(x_rate.get('http_429_count') or 0)}\n"
        "\n"
        "Redis:\n"
        f"Active Locks: {int(redis.get('active_locks') or 0)}\n"
        f"TTL Anomalies: {int(redis.get('ttl_anomalies') or 0)}\n"
        "\n"
        "Risk Assessment:\n"
        f"{risk}"
    )


def _deliver_to_telegram(
    session: Session,
    *,
    workspace_id: str,
    report_text: str,
) -> Dict[str, int]:
    recipients = _owner_admin_chat_ids(session, workspace_id=workspace_id)
    delivered = 0
    failed = 0
    for chat_id in recipients:
        try:
            _send_via_control_telegram_service(chat_id=chat_id, text=report_text)
            delivered += 1
        except Exception:
            failed += 1
            logger.warning(
                "daily_operational_report_delivery_failed",
                workspace_id=workspace_id,
                chat_id=chat_id,
            )
    return {
        "attempted": len(recipients),
        "delivered": delivered,
        "failed": failed,
    }


def run_daily_operational_report(
    session: Session,
    *,
    workspace_id: str,
    redis_client: Redis | None = None,
    now: datetime | None = None,
) -> Dict[str, Any]:
    try:
        resolved_redis_client = redis_client
        if resolved_redis_client is None:
            try:
                resolved_redis_client = get_redis_client()
            except Exception:
                resolved_redis_client = None

        snapshot = build_daily_operational_snapshot(
            session,
            workspace_id=workspace_id,
            redis_client=resolved_redis_client,
            now=now,
        )
        report_text = format_daily_operational_report(snapshot)
        delivery = _deliver_to_telegram(
            session,
            workspace_id=workspace_id,
            report_text=report_text,
        )
        return {
            "status": "ok",
            "workspace_id": workspace_id,
            "snapshot": snapshot,
            "delivery": delivery,
            "report_text": report_text,
        }
    except Exception as exc:
        logger.error(
            "daily_operational_reporter_failed",
            workspace_id=workspace_id,
            error=str(exc),
        )
        return {
            "status": "error",
            "workspace_id": workspace_id,
            "error": str(exc),
            "snapshot": {
                "workspace_id": workspace_id,
                "date_utc": _normalize_dt(now or datetime.now(timezone.utc)).date().isoformat(),
                "mode": "semi_autonomous",
                "editorial_stock": {
                    "pending_review_count": 0,
                    "approved_scheduled_count": 0,
                    "next_window_utc": "n/d",
                    "next_window_key": "n/d",
                    "coverage_days": 0.0,
                    "posts_per_day_target": 0,
                },
                "stability": {
                    "reports": 0,
                    "critical": 0,
                    "high": 0,
                    "auto_containments": 0,
                    "kill_switch": 0,
                },
                "publishing": {
                    "attempts": 0,
                    "success": 0,
                    "failures": 0,
                    "success_rate_pct": 0,
                },
                "circuit_breakers": {
                    "rate_limit_blocks": 0,
                    "consecutive_failure_triggers": 0,
                },
                "x_rate_limits": {"http_429_count": 0},
                "redis": {"active_locks": 0, "ttl_anomalies": 0},
                "risk_assessment": "LOW",
            },
            "delivery": {"attempted": 0, "delivered": 0, "failed": 0},
            "report_text": "",
        }
