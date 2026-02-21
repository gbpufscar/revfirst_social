"""Stability guard agent for runtime reliability, containment, and kill-switch escalation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any, Callable, Dict, List

import httpx
from redis import Redis
from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session

from src.control.security import get_telegram_notification_channel_status, load_admin_directory
from src.control.services import get_workspace_operational_mode, set_operational_mode, set_pause_state
from src.control.state import (
    global_kill_switch_ttl_seconds,
    is_global_kill_switch,
    is_workspace_paused,
    set_global_kill_switch,
    set_workspace_paused,
)
from src.core.config import get_settings
from src.editorial.queue_states import PENDING_REVIEW_STATUSES
from src.integrations.x.service import get_workspace_x_connection_status
from src.storage.models import AdminAction, ApprovalQueueItem, PipelineRun, PublishAuditLog, WorkspaceEvent

_RECOMMENDED_DAILY_POST_PIPELINES = {"daily_post", "execute_approved"}


def _to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _normalize_dt(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _redis_key_to_str(value: Any) -> str:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except Exception:
            return str(value)
    return str(value)


def _event(session: Session, *, workspace_id: str, event_type: str, payload: Dict[str, Any]) -> None:
    session.add(
        WorkspaceEvent(
            workspace_id=workspace_id,
            event_type=event_type,
            payload_json=json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True),
        )
    )


def _check_x_oauth(session: Session, *, workspace_id: str) -> Dict[str, Any]:
    status = get_workspace_x_connection_status(session, workspace_id=workspace_id)
    publish_ready = bool(status.get("publish_ready"))
    if publish_ready:
        return {
            "key": "x_oauth_publish_ready",
            "severity": "ok",
            "status": "pass",
            "summary": "OAuth do X esta conectado e pronto para publicar.",
            "details": status,
            "recommended_action": "Nenhuma acao imediata.",
        }
    return {
        "key": "x_oauth_publish_ready",
        "severity": "critical",
        "status": "fail",
        "summary": f"OAuth do X nao esta pronto para publicar ({status.get('connected_reason') or 'unknown'}).",
        "details": status,
        "recommended_action": "Revalidar /integrations/x/oauth/status e reconectar a conta no fluxo oficial.",
    }


def _check_publish_failures(session: Session, *, workspace_id: str, now: datetime) -> Dict[str, Any]:
    settings = get_settings()
    window_start = now - timedelta(hours=24)
    rows = list(
        session.scalars(
            select(PublishAuditLog)
            .where(
                PublishAuditLog.workspace_id == workspace_id,
                PublishAuditLog.created_at >= window_start,
                PublishAuditLog.action.in_(
                    [
                        "publish_reply",
                        "publish_post",
                        "publish_email",
                        "publish_blog",
                        "publish_instagram",
                    ]
                ),
            )
            .order_by(desc(PublishAuditLog.created_at))
            .limit(200)
        ).all()
    )

    failed_rows = [row for row in rows if row.status == "failed"]
    failed_count = len(failed_rows)
    consecutive_failures = 0
    for row in rows:
        if row.status == "failed":
            consecutive_failures += 1
            continue
        if row.status == "published":
            break

    last_error = failed_rows[0].error_message if failed_rows else None
    if failed_count >= settings.stability_publish_failures_critical_count:
        severity = "critical"
        status = "fail"
        summary = f"Falhas de publicacao nas ultimas 24h: {failed_count}."
        action = "Pausar publicacao e investigar erros de transporte/OAuth antes de retomar."
    elif failed_count >= settings.stability_publish_failures_warning_count:
        severity = "warning"
        status = "warn"
        summary = f"Falhas de publicacao nas ultimas 24h: {failed_count}."
        action = "Revisar logs de publish e repetir apenas apos confirmar causa raiz."
    else:
        severity = "ok"
        status = "pass"
        summary = "Sem falhas de publicacao nas ultimas 24h."
        action = "Nenhuma acao imediata."

    return {
        "key": "publish_failures_24h",
        "severity": severity,
        "status": status,
        "summary": summary,
        "details": {
            "failed_count": failed_count,
            "consecutive_failures": consecutive_failures,
            "last_error": last_error,
            "window_start": _to_iso(window_start),
            "window_end": _to_iso(now),
        },
        "recommended_action": action,
    }


def _check_webhook_command_flow(session: Session, *, workspace_id: str, now: datetime) -> Dict[str, Any]:
    settings = get_settings()
    window_start = now - timedelta(hours=24)
    rows = list(
        session.scalars(
            select(AdminAction)
            .where(
                AdminAction.workspace_id == workspace_id,
                AdminAction.created_at >= window_start,
            )
            .order_by(desc(AdminAction.created_at))
            .limit(50)
        ).all()
    )

    total = len(rows)
    error_count = sum(1 for row in rows if row.status in {"error", "unauthorized"})
    error_rate = round((error_count / total) * 100.0, 2) if total > 0 else 0.0
    last_event_at = _to_iso(rows[0].created_at) if rows else None

    if total >= settings.stability_webhook_min_commands_for_warning and error_rate >= settings.stability_webhook_error_rate_warning_pct:
        severity = "warning"
        status = "warn"
        summary = f"Taxa alta de erro no comando Telegram: {error_rate:.2f}% ({error_count}/{total})."
        action = "Validar comandos recentes e corrigir erros operacionais recorrentes."
    else:
        severity = "ok"
        status = "pass"
        if total == 0:
            summary = "Sem atividade Telegram nas ultimas 24h (sem dados para taxa de erro)."
        else:
            summary = f"Fluxo Telegram estavel: {error_count} erro(s) em {total} comando(s)."
        action = "Nenhuma acao imediata."

    return {
        "key": "telegram_webhook_flow",
        "severity": severity,
        "status": status,
        "summary": summary,
        "details": {
            "total_commands_24h": total,
            "error_commands_24h": error_count,
            "error_rate_pct": error_rate,
            "last_event_at": last_event_at,
        },
        "recommended_action": action,
    }


def _check_notification_channel() -> Dict[str, Any]:
    status = get_telegram_notification_channel_status()
    if status.degraded:
        reasons = sorted(status.reasons)
        return {
            "key": "telegram_notification_channel",
            "severity": "warning",
            "status": "warn",
            "summary": "Notification channel degraded.",
            "details": {
                "status": "degraded",
                "has_bot_token": status.has_bot_token,
                "allowed_ids_count": status.allowed_ids_count,
                "reasons": reasons,
            },
            "recommended_action": "Configurar TELEGRAM_BOT_TOKEN e allowed_telegram_ids antes de depender de alertas proativos.",
        }
    return {
        "key": "telegram_notification_channel",
        "severity": "ok",
        "status": "pass",
        "summary": "Canal de notificacao Telegram saudavel.",
        "details": {
            "status": "healthy",
            "has_bot_token": status.has_bot_token,
            "allowed_ids_count": status.allowed_ids_count,
            "reasons": [],
        },
        "recommended_action": "Nenhuma acao imediata.",
    }


def _check_queue_health(session: Session, *, workspace_id: str, now: datetime) -> Dict[str, Any]:
    settings = get_settings()
    pending_count = int(
        session.scalar(
            select(func.count())
            .select_from(ApprovalQueueItem)
            .where(
                ApprovalQueueItem.workspace_id == workspace_id,
                ApprovalQueueItem.status.in_(PENDING_REVIEW_STATUSES),
            )
        )
        or 0
    )
    publishing_rows = list(
        session.scalars(
            select(ApprovalQueueItem)
            .where(
                ApprovalQueueItem.workspace_id == workspace_id,
                ApprovalQueueItem.status == "publishing",
            )
            .order_by(asc(ApprovalQueueItem.updated_at))
            .limit(10)
        ).all()
    )
    publishing_count = len(publishing_rows)
    oldest_pending = session.scalar(
        select(ApprovalQueueItem.created_at)
        .where(
            ApprovalQueueItem.workspace_id == workspace_id,
            ApprovalQueueItem.status.in_(PENDING_REVIEW_STATUSES),
        )
        .order_by(asc(ApprovalQueueItem.created_at))
        .limit(1)
    )
    oldest_publishing = publishing_rows[0].updated_at if publishing_rows else None

    pending_age_minutes = (
        int((now - _normalize_dt(oldest_pending)).total_seconds() // 60) if oldest_pending is not None else 0
    )
    publishing_age_minutes = (
        int((now - _normalize_dt(oldest_publishing)).total_seconds() // 60) if oldest_publishing is not None else 0
    )

    if publishing_count > 0 and publishing_age_minutes >= settings.stability_queue_publishing_stalled_minutes:
        severity = "critical"
        status = "fail"
        summary = f"Fila possivelmente travada: {publishing_count} item(ns) em publishing ha {publishing_age_minutes} min."
        action = "Pausar workspace e investigar worker/publicador antes de novos approves."
    elif (
        pending_count >= settings.stability_queue_pending_backlog_count
        and pending_age_minutes >= settings.stability_queue_pending_backlog_age_minutes
    ):
        severity = "warning"
        status = "warn"
        summary = f"Backlog alto: {pending_count} pendentes (mais antigo ha {pending_age_minutes} min)."
        action = "Aumentar cadencia de aprovacao/execucao para evitar envelhecimento da fila."
    else:
        severity = "ok"
        status = "pass"
        summary = "Fila de aprovacao sem sinais criticos."
        action = "Nenhuma acao imediata."

    return {
        "key": "approval_queue_health",
        "severity": severity,
        "status": status,
        "summary": summary,
        "details": {
            "pending_count": pending_count,
            "publishing_count": publishing_count,
            "oldest_pending_created_at": _to_iso(oldest_pending),
            "oldest_publishing_updated_at": _to_iso(oldest_publishing),
            "pending_age_minutes": pending_age_minutes,
            "publishing_age_minutes": publishing_age_minutes,
        },
        "recommended_action": action,
    }


def _check_lock_health(redis_client: Redis, *, workspace_id: str) -> Dict[str, Any]:
    settings = get_settings()
    scheduler_lock_key = f"revfirst:{workspace_id}:scheduler:lock"
    run_lock_pattern = f"revfirst:{workspace_id}:control:run:*:lock"
    active_locks: List[str] = []
    if redis_client.exists(scheduler_lock_key):
        active_locks.append(scheduler_lock_key)
    for key in redis_client.keys(run_lock_pattern):
        active_locks.append(_redis_key_to_str(key))

    if len(active_locks) >= settings.stability_lock_stuck_warning_count:
        return {
            "key": "lock_health",
            "severity": "warning",
            "status": "warn",
            "summary": f"Quantidade alta de locks ativos: {len(active_locks)}.",
            "details": {"active_locks": active_locks},
            "recommended_action": "Verificar se ha locks stale no scheduler/control run.",
        }

    return {
        "key": "lock_health",
        "severity": "ok",
        "status": "pass",
        "summary": f"Locks ativos em nivel esperado ({len(active_locks)}).",
        "details": {"active_locks": active_locks},
        "recommended_action": "Nenhuma acao imediata.",
    }


def _check_scheduler_health(session: Session, *, workspace_id: str, now: datetime) -> Dict[str, Any]:
    settings = get_settings()
    window_start = now - timedelta(hours=24)
    rows = list(
        session.scalars(
            select(PipelineRun)
            .where(
                PipelineRun.workspace_id == workspace_id,
                PipelineRun.created_at >= window_start,
                PipelineRun.pipeline_name.in_(_RECOMMENDED_DAILY_POST_PIPELINES),
            )
            .order_by(desc(PipelineRun.created_at))
            .limit(20)
        ).all()
    )
    if not rows:
        return {
            "key": "scheduler_health",
            "severity": "warning",
            "status": "warn",
            "summary": "Sem execucoes recentes de pipelines criticos (daily_post/execute_approved).",
            "details": {"window_hours": 24, "runs": 0},
            "recommended_action": "Validar scheduler, pause state e timer de execucao.",
        }

    failed = [row for row in rows if row.status == "failed"]
    if len(failed) >= settings.stability_scheduler_failures_warning_count:
        return {
            "key": "scheduler_health",
            "severity": "warning",
            "status": "warn",
            "summary": f"Falhas recorrentes em pipelines criticos nas ultimas 24h: {len(failed)}.",
            "details": {
                "window_hours": 24,
                "runs": len(rows),
                "failed": len(failed),
                "latest_failed_pipeline": failed[0].pipeline_name if failed else None,
                "latest_failed_at": _to_iso(failed[0].created_at) if failed else None,
            },
            "recommended_action": "Inspecionar /logs e ultimo erro de pipeline antes de continuar aprovacoes.",
        }

    latest = rows[0]
    return {
        "key": "scheduler_health",
        "severity": "ok",
        "status": "pass",
        "summary": f"Scheduler com atividade recente (ultimo: {latest.pipeline_name}={latest.status}).",
        "details": {
            "window_hours": 24,
            "runs": len(rows),
            "failed": len(failed),
            "latest_pipeline": latest.pipeline_name,
            "latest_status": latest.status,
            "latest_at": _to_iso(latest.created_at),
        },
        "recommended_action": "Nenhuma acao imediata.",
    }


def _check_config_drift() -> Dict[str, Any]:
    settings = get_settings()
    required_pairs = {
        "x_client_id": settings.x_client_id,
        "x_client_secret": settings.x_client_secret,
        "x_redirect_uri": settings.x_redirect_uri,
        "telegram_webhook_secret": settings.telegram_webhook_secret,
        "app_public_base_url": settings.app_public_base_url,
        "publishing_direct_api_internal_key": settings.publishing_direct_api_internal_key,
        "token_encryption_key": settings.token_encryption_key,
    }
    missing_required = [key for key, value in required_pairs.items() if not str(value or "").strip()]

    warnings: List[str] = []
    if not settings.telegram_bot_token.strip():
        warnings.append("telegram_bot_token_missing")
    if settings.env.lower() in {"prod", "production"} and settings.publishing_direct_api_enabled:
        missing_required.append("publishing_direct_api_enabled_must_be_false")

    if missing_required:
        return {
            "key": "config_drift",
            "severity": "critical",
            "status": "fail",
            "summary": "Drift critico de configuracao/secrets detectado.",
            "details": {
                "missing_required": missing_required,
                "warnings": warnings,
                "drift_detected": True,
            },
            "recommended_action": "Corrigir variaveis em producao e redeploy controlado.",
        }

    if warnings:
        return {
            "key": "config_drift",
            "severity": "warning",
            "status": "warn",
            "summary": "Configuracao com avisos nao bloqueantes.",
            "details": {
                "missing_required": [],
                "warnings": warnings,
                "drift_detected": True,
            },
            "recommended_action": "Completar configuracao opcional para melhorar operacao do bot.",
        }

    return {
        "key": "config_drift",
        "severity": "ok",
        "status": "pass",
        "summary": "Secrets e configuracao essenciais estao consistentes.",
        "details": {
            "missing_required": [],
            "warnings": [],
            "drift_detected": False,
        },
        "recommended_action": "Nenhuma acao imediata.",
    }


def _run_check_with_isolation(
    key: str,
    runner: Callable[..., Dict[str, Any]],
    *args: Any,
    **kwargs: Any,
) -> Dict[str, Any]:
    try:
        result = runner(*args, **kwargs)
        if isinstance(result, dict):
            return result
    except Exception as exc:
        return {
            "key": key,
            "severity": "warning",
            "status": "error",
            "summary": f"Check {key} falhou na execucao.",
            "details": {"error": str(exc), "check_error": True},
            "recommended_action": "Inspecionar erro do check e repetir diagnostico.",
        }
    return {
        "key": key,
        "severity": "warning",
        "status": "error",
        "summary": f"Check {key} retornou payload invalido.",
        "details": {"check_error": True},
        "recommended_action": "Inspecionar implementacao do check.",
    }


def evaluate_kill_switch_criteria(report: Dict[str, Any]) -> Dict[str, Any]:
    settings = get_settings()
    checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    check_map: Dict[str, Dict[str, Any]] = {}
    for item in checks:
        if isinstance(item, dict):
            check_map[str(item.get("key") or "")] = item

    oauth_check = check_map.get("x_oauth_publish_ready") or {}
    failure_check = check_map.get("publish_failures_24h") or {}
    queue_check = check_map.get("approval_queue_health") or {}
    lock_check = check_map.get("lock_health") or {}
    drift_check = check_map.get("config_drift") or {}

    failure_details = failure_check.get("details") if isinstance(failure_check.get("details"), dict) else {}
    queue_details = queue_check.get("details") if isinstance(queue_check.get("details"), dict) else {}
    lock_details = lock_check.get("details") if isinstance(lock_check.get("details"), dict) else {}

    oauth_invalid = str(oauth_check.get("status") or "").lower() != "pass"
    publish_failures_24h = int(failure_details.get("failed_count") or 0) >= settings.stability_kill_switch_publish_failures_24h_threshold
    consecutive_publish_failures = int(failure_details.get("consecutive_failures") or 0) >= settings.stability_kill_switch_consecutive_publish_failures_threshold
    queue_stalled_minutes = int(queue_details.get("publishing_age_minutes") or 0) >= settings.stability_kill_switch_queue_stalled_minutes_threshold
    lock_stuck_count = len(lock_details.get("active_locks") or []) >= settings.stability_kill_switch_lock_stuck_count_threshold
    drift_detected = str(drift_check.get("status") or "").lower() in {"fail", "warn"}

    criteria = {
        "oauth_invalid": oauth_invalid,
        "publish_failures_24h": publish_failures_24h,
        "consecutive_publish_failures": consecutive_publish_failures,
        "queue_stalled_minutes": queue_stalled_minutes,
        "lock_stuck_count": lock_stuck_count,
        "drift_detected": drift_detected,
    }
    true_reasons = [key for key, enabled in criteria.items() if enabled]
    true_count = len(true_reasons)

    threshold = settings.stability_kill_switch_criteria_threshold
    enabled = bool(settings.stability_kill_switch_enabled)
    triggered = enabled and true_count >= threshold
    return {
        "enabled": enabled,
        "threshold": threshold,
        "true_count": true_count,
        "criteria": criteria,
        "triggered": triggered,
        "trigger_reasons": true_reasons,
    }


def _send_proactive_telegram_alert(*, title: str, lines: List[str]) -> Dict[str, Any]:
    settings = get_settings()
    token = settings.telegram_bot_token.strip()
    if not token:
        return {"sent": 0, "skipped": "telegram_bot_token_missing"}

    directory = load_admin_directory()
    recipients = sorted(directory.allowed_telegram_ids)
    if not recipients:
        return {"sent": 0, "skipped": "telegram_admin_directory_empty"}

    message = "\n".join([title, *[str(value) for value in lines if str(value).strip()]])
    sent = 0
    errors: List[str] = []
    with httpx.Client(timeout=10) as client:
        for chat_id in recipients:
            try:
                response = client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": message[:4096],
                        "disable_web_page_preview": True,
                    },
                )
                if response.status_code >= 400:
                    errors.append(f"{chat_id}:{response.status_code}")
                    continue
                sent += 1
            except Exception as exc:
                errors.append(f"{chat_id}:{exc}")

    return {"sent": sent, "attempted": len(recipients), "errors": errors}


def build_workspace_stability_report(
    session: Session,
    *,
    workspace_id: str,
    redis_client: Redis,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    checks = [
        _run_check_with_isolation("x_oauth_publish_ready", _check_x_oauth, session, workspace_id=workspace_id),
        _run_check_with_isolation("telegram_notification_channel", _check_notification_channel),
        _run_check_with_isolation("publish_failures_24h", _check_publish_failures, session, workspace_id=workspace_id, now=now),
        _run_check_with_isolation("telegram_webhook_flow", _check_webhook_command_flow, session, workspace_id=workspace_id, now=now),
        _run_check_with_isolation("approval_queue_health", _check_queue_health, session, workspace_id=workspace_id, now=now),
        _run_check_with_isolation("lock_health", _check_lock_health, redis_client, workspace_id=workspace_id),
        _run_check_with_isolation("scheduler_health", _check_scheduler_health, session, workspace_id=workspace_id, now=now),
        _run_check_with_isolation("config_drift", _check_config_drift),
    ]

    critical_count = sum(1 for item in checks if item.get("severity") == "critical")
    warning_count = sum(1 for item in checks if item.get("severity") == "warning")
    error_count = sum(1 for item in checks if item.get("status") == "error")
    overall_status = "critical" if critical_count > 0 else ("warning" if warning_count > 0 else "healthy")

    recommended_actions = [
        str(item.get("recommended_action"))
        for item in checks
        if item.get("severity") in {"critical", "warning"} and str(item.get("recommended_action")).strip()
    ]

    report = {
        "workspace_id": workspace_id,
        "generated_at": _to_iso(now),
        "overall_status": overall_status,
        "critical_count": critical_count,
        "warning_count": warning_count,
        "check_error_count": error_count,
        "checks": checks,
        "recommended_actions": recommended_actions[:5],
        "containment_recommended": critical_count > 0,
        "current_mode": get_workspace_operational_mode(session, workspace_id=workspace_id, redis_client=redis_client),
    }
    report["kill_switch"] = evaluate_kill_switch_criteria(report)

    _event(
        session,
        workspace_id=workspace_id,
        event_type="stability_guard_report_generated",
        payload={
            "overall_status": overall_status,
            "critical_count": critical_count,
            "warning_count": warning_count,
            "check_error_count": error_count,
            "containment_recommended": report["containment_recommended"],
            "kill_switch": report["kill_switch"],
        },
    )
    session.commit()
    return report


def apply_stability_containment(
    session: Session,
    *,
    workspace_id: str,
    redis_client: Redis,
    report: Dict[str, Any],
    actor_user_id: str | None = None,
    trigger: str = "manual",
) -> Dict[str, Any]:
    overall_status = str(report.get("overall_status") or "unknown").strip().lower()
    is_critical = overall_status == "critical"
    actions_applied: List[str] = []
    already_paused = is_workspace_paused(redis_client, workspace_id=workspace_id)
    previous_mode = get_workspace_operational_mode(session, workspace_id=workspace_id, redis_client=redis_client)

    if is_critical:
        if not already_paused:
            set_pause_state(session, workspace_id=workspace_id, paused=True)
            set_workspace_paused(redis_client, workspace_id=workspace_id, paused=True)
            actions_applied.append("workspace_paused")

        if previous_mode != "containment":
            set_operational_mode(
                session,
                workspace_id=workspace_id,
                mode="containment",
                changed_by_user_id=actor_user_id,
                redis_client=redis_client,
            )
            actions_applied.append("mode_containment")

    current_mode = get_workspace_operational_mode(session, workspace_id=workspace_id, redis_client=redis_client)
    payload = {
        "requested": True,
        "trigger": trigger,
        "overall_status": overall_status,
        "containment_recommended": bool(report.get("containment_recommended")),
        "already_paused": already_paused,
        "previous_mode": previous_mode,
        "current_mode": current_mode,
        "actions_applied": actions_applied,
    }
    _event(
        session,
        workspace_id=workspace_id,
        event_type="stability_guard_containment",
        payload=payload,
    )
    session.commit()
    return payload


def _apply_kill_switch_if_needed(
    session: Session,
    *,
    workspace_id: str,
    redis_client: Redis,
    report: Dict[str, Any],
    trigger: str,
) -> Dict[str, Any]:
    kill_switch = report.get("kill_switch") if isinstance(report.get("kill_switch"), dict) else {}
    if not bool(kill_switch.get("triggered")):
        return {
            "applied": False,
            "triggered": False,
            "reasons": kill_switch.get("trigger_reasons", []),
        }

    settings = get_settings()
    ttl_seconds = settings.stability_kill_switch_ttl_seconds
    set_global_kill_switch(redis_client, enabled=True, ttl_seconds=ttl_seconds)
    payload = {
        "applied": True,
        "triggered": True,
        "trigger": trigger,
        "reasons": kill_switch.get("trigger_reasons", []),
        "ttl_seconds": ttl_seconds,
    }
    _event(
        session,
        workspace_id=workspace_id,
        event_type="stability_guard_kill_switch",
        payload=payload,
    )
    session.commit()

    _send_proactive_telegram_alert(
        title="[RevFirst] Kill-switch global ativado",
        lines=[
            f"workspace: {workspace_id}",
            f"motivos: {', '.join(payload['reasons']) if payload['reasons'] else 'n/d'}",
            f"ttl: {ttl_seconds}s",
            "Acao: valide /stability e use /ack_kill_switch se precisar estender janela.",
        ],
    )
    return payload


def ack_global_kill_switch(
    session: Session,
    *,
    workspace_id: str,
    redis_client: Redis,
    actor_user_id: str | None,
) -> Dict[str, Any]:
    if not is_global_kill_switch(redis_client):
        return {
            "acknowledged": False,
            "enabled": False,
            "ttl_seconds": None,
            "message": "global_kill_switch_not_enabled",
        }

    settings = get_settings()
    ttl_seconds = settings.stability_kill_switch_ack_ttl_seconds
    set_global_kill_switch(redis_client, enabled=True, ttl_seconds=ttl_seconds)
    payload = {
        "acknowledged": True,
        "enabled": True,
        "ttl_seconds": ttl_seconds,
        "acked_by_user_id": actor_user_id,
    }
    _event(
        session,
        workspace_id=workspace_id,
        event_type="stability_guard_kill_switch_ack",
        payload=payload,
    )
    session.commit()
    return payload


def run_workspace_stability_guard_cycle(
    session: Session,
    *,
    workspace_id: str,
    redis_client: Redis,
    actor_user_id: str | None = None,
    trigger: str = "manual",
) -> Dict[str, Any]:
    report = build_workspace_stability_report(
        session,
        workspace_id=workspace_id,
        redis_client=redis_client,
    )

    containment = {
        "requested": False,
        "actions_applied": [],
    }
    settings = get_settings()
    if settings.stability_auto_containment_on_critical and str(report.get("overall_status") or "").lower() == "critical":
        containment = apply_stability_containment(
            session,
            workspace_id=workspace_id,
            redis_client=redis_client,
            report=report,
            actor_user_id=actor_user_id,
            trigger=trigger,
        )
        _send_proactive_telegram_alert(
            title="[RevFirst] Auto-containment aplicado",
            lines=[
                f"workspace: {workspace_id}",
                f"status: {report.get('overall_status')}",
                f"acoes: {', '.join(containment.get('actions_applied', [])) or 'nenhuma'}",
                "Acao: use /stability para diagnostico completo.",
            ],
        )

    kill_switch = _apply_kill_switch_if_needed(
        session,
        workspace_id=workspace_id,
        redis_client=redis_client,
        report=report,
        trigger=trigger,
    )

    report["containment"] = containment
    report["kill_switch_state"] = {
        "enabled": is_global_kill_switch(redis_client),
        "ttl_seconds": global_kill_switch_ttl_seconds(redis_client),
    }
    report["kill_switch_action"] = kill_switch
    return report
