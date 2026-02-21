"""Publishing engine for channel writers with shared guardrails."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from typing import Any, Dict, Optional
import uuid

import httpx
from src.channels.base import ChannelPayload
from src.channels.blog.publisher import BlogPublisher
from src.channels.email.publisher import EmailPublisher
from src.channels.instagram.publisher import InstagramPublisher
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.billing.plans import check_plan_limit, record_usage
from src.control.security import load_admin_directory
from src.control.services import (
    get_workspace_operational_mode,
    publishing_allowed_for_mode,
    set_operational_mode,
    set_pause_state,
)
from src.control.state import is_workspace_paused, set_workspace_paused
from src.core.config import get_settings
from src.core.metrics import (
    record_publish_error,
    record_replies_published,
    record_reply_blocked,
)
from src.integrations.x.service import get_workspace_x_access_token
from src.integrations.x.x_client import XClient, XClientError
from src.storage.models import PublishAuditLog, PublishCooldown, WorkspaceEvent
from src.storage.redis_client import get_client as get_redis_client


@dataclass(frozen=True)
class PublishResult:
    workspace_id: str
    action: str
    published: bool
    external_post_id: Optional[str]
    status: str
    message: str


def _json_dumps(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True)


def _normalize_dt(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _create_audit_log(
    session: Session,
    *,
    platform: str,
    workspace_id: str,
    action: str,
    text: str,
    status: str,
    in_reply_to_tweet_id: Optional[str] = None,
    target_thread_id: Optional[str] = None,
    target_author_id: Optional[str] = None,
    external_post_id: Optional[str] = None,
    error_message: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> PublishAuditLog:
    log = PublishAuditLog(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        platform=platform,
        action=action,
        request_text=text,
        in_reply_to_tweet_id=in_reply_to_tweet_id,
        target_thread_id=target_thread_id,
        target_author_id=target_author_id,
        external_post_id=external_post_id,
        status=status,
        error_message=error_message[:255] if error_message else None,
        payload_json=_json_dumps(payload or {}),
    )
    session.add(log)
    return log


def _check_cooldown(
    session: Session,
    *,
    workspace_id: str,
    scope: str,
    scope_key: Optional[str],
) -> Optional[datetime]:
    if not scope_key:
        return None
    record = session.scalar(
        select(PublishCooldown).where(
            PublishCooldown.workspace_id == workspace_id,
            PublishCooldown.scope == scope,
            PublishCooldown.scope_key == scope_key,
        )
    )
    if record is None:
        return None
    now = datetime.now(timezone.utc)
    cooldown_until = _normalize_dt(record.cooldown_until)
    if cooldown_until > now:
        return cooldown_until
    return None


def _upsert_cooldown(
    session: Session,
    *,
    workspace_id: str,
    scope: str,
    scope_key: Optional[str],
    action: str,
    cooldown_minutes: int,
) -> None:
    if not scope_key:
        return

    cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=cooldown_minutes)
    record = session.scalar(
        select(PublishCooldown).where(
            PublishCooldown.workspace_id == workspace_id,
            PublishCooldown.scope == scope,
            PublishCooldown.scope_key == scope_key,
        )
    )
    if record is None:
        record = PublishCooldown(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            scope=scope,
            scope_key=scope_key,
            cooldown_until=cooldown_until,
            last_action=action,
        )
        session.add(record)
    else:
        record.cooldown_until = cooldown_until
        record.last_action = action
        record.updated_at = datetime.now(timezone.utc)


def _extract_external_post_id(payload: Dict[str, Any]) -> Optional[str]:
    data = payload.get("data")
    if isinstance(data, dict) and data.get("id"):
        return str(data["id"])
    if payload.get("id"):
        return str(payload["id"])
    return None


def _mode_block_message(mode: str) -> str:
    return f"Publishing blocked by operational mode: {mode}"


_REPLY_HOURLY_COUNTER_KEY = "revfirst:{workspace_id}:publishing:replies:{hour_bucket}"
_CONSECUTIVE_FAILURE_COUNTER_KEY = "revfirst:{workspace_id}:publishing:consecutive_failures"
_CONSECUTIVE_FAILURE_ALERT_KEY = "revfirst:{workspace_id}:publishing:circuit_breaker_alert"
_CONSECUTIVE_FAILURE_COUNTER_TTL_SECONDS = 86400
_CONSECUTIVE_FAILURE_ALERT_TTL_SECONDS = 1800


def _get_redis_client_safe():
    try:
        return get_redis_client()
    except Exception:
        return None


def _redis_get_int(redis_client: Any, key: str) -> int:
    try:
        value = redis_client.get(key)
        if value is None:
            return 0
        return int(value)
    except Exception:
        return 0


def _utc_hour_bucket(now: datetime) -> str:
    return now.strftime("%Y%m%d%H")


def _seconds_until_next_hour(now: datetime) -> int:
    base = now.replace(minute=0, second=0, microsecond=0)
    next_hour = base + timedelta(hours=1)
    return max(1, int((next_hour - now).total_seconds()))


def _reply_hour_counter_key(*, workspace_id: str, now: datetime) -> str:
    return _REPLY_HOURLY_COUNTER_KEY.format(workspace_id=workspace_id, hour_bucket=_utc_hour_bucket(now))


def _failure_counter_key(*, workspace_id: str) -> str:
    return _CONSECUTIVE_FAILURE_COUNTER_KEY.format(workspace_id=workspace_id)


def _failure_alert_key(*, workspace_id: str) -> str:
    return _CONSECUTIVE_FAILURE_ALERT_KEY.format(workspace_id=workspace_id)


def _get_consecutive_publish_failures(workspace_id: str) -> int:
    redis_client = _get_redis_client_safe()
    if redis_client is None:
        return 0
    return _redis_get_int(redis_client, _failure_counter_key(workspace_id=workspace_id))


def _increment_consecutive_publish_failures(workspace_id: str) -> int | None:
    redis_client = _get_redis_client_safe()
    if redis_client is None:
        return None
    key = _failure_counter_key(workspace_id=workspace_id)
    try:
        failures = int(redis_client.incr(key))
        if failures == 1:
            redis_client.expire(key, _CONSECUTIVE_FAILURE_COUNTER_TTL_SECONDS)
        return failures
    except Exception:
        return None


def _reset_consecutive_publish_failures(workspace_id: str) -> None:
    redis_client = _get_redis_client_safe()
    if redis_client is None:
        return
    try:
        redis_client.delete(_failure_counter_key(workspace_id=workspace_id))
        redis_client.delete(_failure_alert_key(workspace_id=workspace_id))
    except Exception:
        return


def _increment_reply_hour_counter(workspace_id: str) -> int | None:
    settings = get_settings()
    if settings.max_replies_per_hour <= 0:
        return None

    redis_client = _get_redis_client_safe()
    if redis_client is None:
        return None

    now = datetime.now(timezone.utc)
    key = _reply_hour_counter_key(workspace_id=workspace_id, now=now)
    ttl_seconds = _seconds_until_next_hour(now) + 5
    try:
        count = int(redis_client.incr(key))
        if count == 1:
            redis_client.expire(key, ttl_seconds)
        return count
    except Exception:
        return None


def _send_publish_circuit_breaker_alert(
    *,
    workspace_id: str,
    action: str,
    failures: int,
    threshold: int,
    error_message: str,
    actions_applied: list[str],
) -> None:
    settings = get_settings()
    token = settings.telegram_bot_token.strip()
    if not token:
        return

    directory = load_admin_directory()
    recipients = sorted(directory.allowed_telegram_ids)
    if not recipients:
        return

    lines = [
        "[RevFirst] Circuit breaker de publicacao ativado",
        f"workspace: {workspace_id}",
        f"acao: {action}",
        f"falhas consecutivas: {failures} (limite={threshold})",
        f"erro recente: {error_message or 'n/d'}",
        f"contencao: {', '.join(actions_applied) if actions_applied else 'nenhuma'}",
        "acao: owner pode publicar com override apos diagnostico.",
    ]
    message = "\n".join(lines)[:4096]
    with httpx.Client(timeout=10) as client:
        for chat_id in recipients:
            try:
                client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": message,
                        "disable_web_page_preview": True,
                    },
                )
            except Exception:
                continue


def _apply_publish_failure_containment(
    session: Session,
    *,
    workspace_id: str,
    action: str,
    failure_count: int,
    threshold: int,
    error_message: str,
) -> None:
    redis_client = _get_redis_client_safe()
    if redis_client is None:
        return

    actions_applied: list[str] = []
    paused = is_workspace_paused(redis_client, workspace_id=workspace_id)
    mode = get_workspace_operational_mode(session, workspace_id=workspace_id, redis_client=redis_client)

    if not paused:
        set_pause_state(session, workspace_id=workspace_id, paused=True)
        set_workspace_paused(redis_client, workspace_id=workspace_id, paused=True)
        actions_applied.append("workspace_paused")

    if mode != "containment":
        set_operational_mode(
            session,
            workspace_id=workspace_id,
            mode="containment",
            changed_by_user_id=None,
            redis_client=redis_client,
        )
        actions_applied.append("mode_containment")

    session.add(
        WorkspaceEvent(
            workspace_id=workspace_id,
            event_type="publishing_circuit_breaker_triggered",
            payload_json=_json_dumps(
                {
                    "action": action,
                    "failure_count": failure_count,
                    "threshold": threshold,
                    "error_message": error_message,
                    "actions_applied": actions_applied,
                }
            ),
        )
    )
    session.commit()

    should_alert = True
    alert_key = _failure_alert_key(workspace_id=workspace_id)
    try:
        should_alert = bool(
            redis_client.set(
                alert_key,
                "true",
                nx=True,
                ex=_CONSECUTIVE_FAILURE_ALERT_TTL_SECONDS,
            )
        )
    except Exception:
        should_alert = True

    if should_alert:
        _send_publish_circuit_breaker_alert(
            workspace_id=workspace_id,
            action=action,
            failures=failure_count,
            threshold=threshold,
            error_message=error_message,
            actions_applied=actions_applied,
        )


def _register_publish_failure(
    session: Session,
    *,
    workspace_id: str,
    action: str,
    error_message: str,
) -> None:
    settings = get_settings()
    threshold = settings.max_consecutive_publish_failures
    if threshold <= 0:
        return

    failure_count = _increment_consecutive_publish_failures(workspace_id)
    if failure_count is None:
        return
    if failure_count < threshold:
        return

    _apply_publish_failure_containment(
        session,
        workspace_id=workspace_id,
        action=action,
        failure_count=failure_count,
        threshold=threshold,
        error_message=error_message,
    )


def _guard_consecutive_failure_breaker(
    session: Session,
    *,
    workspace_id: str,
    action: str,
    text: str,
    platform: str,
    owner_override: bool = False,
    in_reply_to_tweet_id: Optional[str] = None,
    target_thread_id: Optional[str] = None,
    target_author_id: Optional[str] = None,
) -> Optional[PublishResult]:
    settings = get_settings()
    threshold = settings.max_consecutive_publish_failures
    if threshold <= 0 or owner_override:
        return None

    failures = _get_consecutive_publish_failures(workspace_id)
    if failures < threshold:
        return None

    message = (
        f"Publishing blocked by circuit breaker ({failures} consecutive failures, threshold={threshold}). "
        "Use owner override after investigation."
    )
    if action == "publish_reply":
        record_reply_blocked(workspace_id=workspace_id, reason="circuit_breaker")
    _create_audit_log(
        session,
        platform=platform,
        workspace_id=workspace_id,
        action=action,
        text=text,
        status="blocked_circuit_breaker",
        in_reply_to_tweet_id=in_reply_to_tweet_id,
        target_thread_id=target_thread_id,
        target_author_id=target_author_id,
        error_message=message,
        payload={
            "consecutive_failures": failures,
            "threshold": threshold,
        },
    )
    session.commit()
    return PublishResult(
        workspace_id=workspace_id,
        action=action,
        published=False,
        external_post_id=None,
        status="blocked_circuit_breaker",
        message=message,
    )


def _guard_reply_hour_quota(
    session: Session,
    *,
    workspace_id: str,
    action: str,
    text: str,
    in_reply_to_tweet_id: str,
    thread_id: Optional[str],
    target_author_id: Optional[str],
    owner_override: bool = False,
) -> Optional[PublishResult]:
    settings = get_settings()
    limit = settings.max_replies_per_hour
    if limit <= 0 or owner_override:
        return None

    redis_client = _get_redis_client_safe()
    if redis_client is None:
        return None

    now = datetime.now(timezone.utc)
    key = _reply_hour_counter_key(workspace_id=workspace_id, now=now)
    used = _redis_get_int(redis_client, key)
    if used < limit:
        return None

    record_reply_blocked(workspace_id=workspace_id, reason="hour_quota")
    message = f"Reply hourly quota exceeded ({used}/{limit})."
    _create_audit_log(
        session,
        platform="x",
        workspace_id=workspace_id,
        action=action,
        text=text,
        status="blocked_rate_limit",
        in_reply_to_tweet_id=in_reply_to_tweet_id,
        target_thread_id=thread_id,
        target_author_id=target_author_id,
        error_message=message,
        payload={
            "max_replies_per_hour": limit,
            "used_current_hour": used,
            "hour_bucket_utc": _utc_hour_bucket(now),
        },
    )
    session.commit()
    return PublishResult(
        workspace_id=workspace_id,
        action=action,
        published=False,
        external_post_id=None,
        status="blocked_rate_limit",
        message=message,
    )


def _guard_operational_mode(
    session: Session,
    *,
    workspace_id: str,
    action: str,
    text: str,
    platform: str,
    owner_override: bool = False,
    in_reply_to_tweet_id: Optional[str] = None,
    target_thread_id: Optional[str] = None,
    target_author_id: Optional[str] = None,
) -> Optional[PublishResult]:
    mode = get_workspace_operational_mode(session, workspace_id=workspace_id)
    if publishing_allowed_for_mode(mode, owner_override=owner_override):
        return None

    _create_audit_log(
        session,
        platform=platform,
        workspace_id=workspace_id,
        action=action,
        text=text,
        status="blocked_mode",
        in_reply_to_tweet_id=in_reply_to_tweet_id,
        target_thread_id=target_thread_id,
        target_author_id=target_author_id,
        error_message=_mode_block_message(mode),
        payload={"mode": mode},
    )
    session.commit()
    return PublishResult(
        workspace_id=workspace_id,
        action=action,
        published=False,
        external_post_id=None,
        status="blocked_mode",
        message=_mode_block_message(mode),
    )


def publish_reply(
    session: Session,
    *,
    workspace_id: str,
    text: str,
    in_reply_to_tweet_id: str,
    thread_id: Optional[str],
    target_author_id: Optional[str],
    x_client: XClient,
    owner_override: bool = False,
) -> PublishResult:
    action = "publish_reply"
    mode_block = _guard_operational_mode(
        session,
        workspace_id=workspace_id,
        action=action,
        text=text,
        platform="x",
        owner_override=owner_override,
        in_reply_to_tweet_id=in_reply_to_tweet_id,
        target_thread_id=thread_id,
        target_author_id=target_author_id,
    )
    if mode_block is not None:
        return mode_block

    breaker_block = _guard_consecutive_failure_breaker(
        session,
        workspace_id=workspace_id,
        action=action,
        text=text,
        platform="x",
        owner_override=owner_override,
        in_reply_to_tweet_id=in_reply_to_tweet_id,
        target_thread_id=thread_id,
        target_author_id=target_author_id,
    )
    if breaker_block is not None:
        return breaker_block

    quota_block = _guard_reply_hour_quota(
        session,
        workspace_id=workspace_id,
        action=action,
        text=text,
        in_reply_to_tweet_id=in_reply_to_tweet_id,
        thread_id=thread_id,
        target_author_id=target_author_id,
        owner_override=owner_override,
    )
    if quota_block is not None:
        return quota_block

    settings = get_settings()
    token = get_workspace_x_access_token(session, workspace_id=workspace_id)
    if token is None:
        record_publish_error(workspace_id=workspace_id, channel="x")
        _create_audit_log(
            session,
            platform="x",
            workspace_id=workspace_id,
            action=action,
            text=text,
            status="failed",
            in_reply_to_tweet_id=in_reply_to_tweet_id,
            target_thread_id=thread_id,
            target_author_id=target_author_id,
            error_message="Workspace X OAuth is missing or expired",
        )
        session.commit()
        _register_publish_failure(
            session,
            workspace_id=workspace_id,
            action=action,
            error_message="Workspace X OAuth is missing or expired",
        )
        return PublishResult(
            workspace_id=workspace_id,
            action=action,
            published=False,
            external_post_id=None,
            status="failed",
            message="Workspace X OAuth is missing or expired",
        )

    limit_decision = check_plan_limit(
        session,
        workspace_id=workspace_id,
        action=action,
        requested=1,
    )
    if not limit_decision.allowed:
        record_reply_blocked(workspace_id=workspace_id, reason="plan_limit")
        _create_audit_log(
            session,
            platform="x",
            workspace_id=workspace_id,
            action=action,
            text=text,
            status="blocked_plan",
            in_reply_to_tweet_id=in_reply_to_tweet_id,
            target_thread_id=thread_id,
            target_author_id=target_author_id,
            error_message="Plan limit exceeded",
            payload={
                "limit": limit_decision.limit,
                "used": limit_decision.used,
                "requested": 1,
            },
        )
        session.commit()
        return PublishResult(
            workspace_id=workspace_id,
            action=action,
            published=False,
            external_post_id=None,
            status="blocked_plan",
            message="Plan limit exceeded",
        )

    blocked_thread_until = _check_cooldown(
        session,
        workspace_id=workspace_id,
        scope="thread",
        scope_key=thread_id or in_reply_to_tweet_id,
    )
    if blocked_thread_until is not None:
        record_reply_blocked(workspace_id=workspace_id, reason="thread_cooldown")
        _create_audit_log(
            session,
            platform="x",
            workspace_id=workspace_id,
            action=action,
            text=text,
            status="blocked_cooldown",
            in_reply_to_tweet_id=in_reply_to_tweet_id,
            target_thread_id=thread_id,
            target_author_id=target_author_id,
            error_message=f"Thread cooldown active until {blocked_thread_until.isoformat()}",
        )
        session.commit()
        return PublishResult(
            workspace_id=workspace_id,
            action=action,
            published=False,
            external_post_id=None,
            status="blocked_cooldown",
            message="Thread cooldown active",
        )

    blocked_author_until = _check_cooldown(
        session,
        workspace_id=workspace_id,
        scope="author",
        scope_key=target_author_id,
    )
    if blocked_author_until is not None:
        record_reply_blocked(workspace_id=workspace_id, reason="author_cooldown")
        _create_audit_log(
            session,
            platform="x",
            workspace_id=workspace_id,
            action=action,
            text=text,
            status="blocked_cooldown",
            in_reply_to_tweet_id=in_reply_to_tweet_id,
            target_thread_id=thread_id,
            target_author_id=target_author_id,
            error_message=f"Author cooldown active until {blocked_author_until.isoformat()}",
        )
        session.commit()
        return PublishResult(
            workspace_id=workspace_id,
            action=action,
            published=False,
            external_post_id=None,
            status="blocked_cooldown",
            message="Author cooldown active",
        )

    try:
        publish_payload = x_client.create_tweet(
            access_token=token,
            text=text,
            in_reply_to_tweet_id=in_reply_to_tweet_id,
        )
        external_post_id = _extract_external_post_id(publish_payload)

        _create_audit_log(
            session,
            platform="x",
            workspace_id=workspace_id,
            action=action,
            text=text,
            status="published",
            in_reply_to_tweet_id=in_reply_to_tweet_id,
            target_thread_id=thread_id,
            target_author_id=target_author_id,
            external_post_id=external_post_id,
            payload=publish_payload,
        )
        record_usage(
            session,
            workspace_id=workspace_id,
            action=action,
            amount=1,
            payload={"external_post_id": external_post_id},
        )
        _upsert_cooldown(
            session,
            workspace_id=workspace_id,
            scope="thread",
            scope_key=thread_id or in_reply_to_tweet_id,
            action=action,
            cooldown_minutes=settings.publish_thread_cooldown_minutes,
        )
        _upsert_cooldown(
            session,
            workspace_id=workspace_id,
            scope="author",
            scope_key=target_author_id,
            action=action,
            cooldown_minutes=settings.publish_author_cooldown_minutes,
        )
        session.add(
            WorkspaceEvent(
                workspace_id=workspace_id,
                event_type="publish_reply",
                payload_json=_json_dumps(
                    {
                        "external_post_id": external_post_id,
                        "thread_id": thread_id,
                        "target_author_id": target_author_id,
                    }
                ),
            )
        )
        session.commit()
        _increment_reply_hour_counter(workspace_id)
        _reset_consecutive_publish_failures(workspace_id)
        record_replies_published(workspace_id=workspace_id)
        return PublishResult(
            workspace_id=workspace_id,
            action=action,
            published=True,
            external_post_id=external_post_id,
            status="published",
            message="Reply published",
        )
    except XClientError as exc:
        session.rollback()
        record_publish_error(workspace_id=workspace_id, channel="x")
        _create_audit_log(
            session,
            platform="x",
            workspace_id=workspace_id,
            action=action,
            text=text,
            status="failed",
            in_reply_to_tweet_id=in_reply_to_tweet_id,
            target_thread_id=thread_id,
            target_author_id=target_author_id,
            error_message=str(exc),
        )
        session.commit()
        _register_publish_failure(
            session,
            workspace_id=workspace_id,
            action=action,
            error_message=str(exc),
        )
        return PublishResult(
            workspace_id=workspace_id,
            action=action,
            published=False,
            external_post_id=None,
            status="failed",
            message="X publish failed",
        )
    except Exception:
        session.rollback()
        record_publish_error(workspace_id=workspace_id, channel="x")
        _create_audit_log(
            session,
            platform="x",
            workspace_id=workspace_id,
            action=action,
            text=text,
            status="failed",
            in_reply_to_tweet_id=in_reply_to_tweet_id,
            target_thread_id=thread_id,
            target_author_id=target_author_id,
            error_message="unexpected_publish_error",
        )
        session.commit()
        _register_publish_failure(
            session,
            workspace_id=workspace_id,
            action=action,
            error_message="unexpected_publish_error",
        )
        return PublishResult(
            workspace_id=workspace_id,
            action=action,
            published=False,
            external_post_id=None,
            status="failed",
            message="X publish failed",
        )


def publish_post(
    session: Session,
    *,
    workspace_id: str,
    text: str,
    x_client: XClient,
    owner_override: bool = False,
) -> PublishResult:
    action = "publish_post"
    mode_block = _guard_operational_mode(
        session,
        workspace_id=workspace_id,
        action=action,
        text=text,
        platform="x",
        owner_override=owner_override,
    )
    if mode_block is not None:
        return mode_block

    breaker_block = _guard_consecutive_failure_breaker(
        session,
        workspace_id=workspace_id,
        action=action,
        text=text,
        platform="x",
        owner_override=owner_override,
    )
    if breaker_block is not None:
        return breaker_block

    token = get_workspace_x_access_token(session, workspace_id=workspace_id)
    if token is None:
        record_publish_error(workspace_id=workspace_id, channel="x")
        _create_audit_log(
            session,
            platform="x",
            workspace_id=workspace_id,
            action=action,
            text=text,
            status="failed",
            error_message="Workspace X OAuth is missing or expired",
        )
        session.commit()
        _register_publish_failure(
            session,
            workspace_id=workspace_id,
            action=action,
            error_message="Workspace X OAuth is missing or expired",
        )
        return PublishResult(
            workspace_id=workspace_id,
            action=action,
            published=False,
            external_post_id=None,
            status="failed",
            message="Workspace X OAuth is missing or expired",
        )

    limit_decision = check_plan_limit(
        session,
        workspace_id=workspace_id,
        action=action,
        requested=1,
    )
    if not limit_decision.allowed:
        _create_audit_log(
            session,
            platform="x",
            workspace_id=workspace_id,
            action=action,
            text=text,
            status="blocked_plan",
            error_message="Plan limit exceeded",
            payload={
                "limit": limit_decision.limit,
                "used": limit_decision.used,
                "requested": 1,
            },
        )
        session.commit()
        return PublishResult(
            workspace_id=workspace_id,
            action=action,
            published=False,
            external_post_id=None,
            status="blocked_plan",
            message="Plan limit exceeded",
        )

    try:
        publish_payload = x_client.create_tweet(access_token=token, text=text)
        external_post_id = _extract_external_post_id(publish_payload)
        _create_audit_log(
            session,
            platform="x",
            workspace_id=workspace_id,
            action=action,
            text=text,
            status="published",
            external_post_id=external_post_id,
            payload=publish_payload,
        )
        record_usage(
            session,
            workspace_id=workspace_id,
            action=action,
            amount=1,
            payload={"external_post_id": external_post_id},
        )
        session.add(
            WorkspaceEvent(
                workspace_id=workspace_id,
                event_type="publish_post",
                payload_json=_json_dumps({"external_post_id": external_post_id}),
            )
        )
        session.commit()
        _reset_consecutive_publish_failures(workspace_id)
        return PublishResult(
            workspace_id=workspace_id,
            action=action,
            published=True,
            external_post_id=external_post_id,
            status="published",
            message="Post published",
        )
    except XClientError as exc:
        session.rollback()
        record_publish_error(workspace_id=workspace_id, channel="x")
        _create_audit_log(
            session,
            platform="x",
            workspace_id=workspace_id,
            action=action,
            text=text,
            status="failed",
            error_message=str(exc),
        )
        session.commit()
        _register_publish_failure(
            session,
            workspace_id=workspace_id,
            action=action,
            error_message=str(exc),
        )
        return PublishResult(
            workspace_id=workspace_id,
            action=action,
            published=False,
            external_post_id=None,
            status="failed",
            message="X publish failed",
        )
    except Exception:
        session.rollback()
        record_publish_error(workspace_id=workspace_id, channel="x")
        _create_audit_log(
            session,
            platform="x",
            workspace_id=workspace_id,
            action=action,
            text=text,
            status="failed",
            error_message="unexpected_publish_error",
        )
        session.commit()
        _register_publish_failure(
            session,
            workspace_id=workspace_id,
            action=action,
            error_message="unexpected_publish_error",
        )
        return PublishResult(
            workspace_id=workspace_id,
            action=action,
            published=False,
            external_post_id=None,
            status="failed",
            message="X publish failed",
        )


def publish_email(
    session: Session,
    *,
    workspace_id: str,
    subject: str,
    body: str,
    recipients: Optional[list[str]] = None,
    email_publisher: Optional[EmailPublisher] = None,
    source_kind: Optional[str] = None,
    source_ref_id: Optional[str] = None,
    owner_override: bool = False,
) -> PublishResult:
    action = "publish_email"
    mode_block = _guard_operational_mode(
        session,
        workspace_id=workspace_id,
        action=action,
        text=body,
        platform="email",
        owner_override=owner_override,
    )
    if mode_block is not None:
        return mode_block
    publisher = email_publisher or EmailPublisher()

    limit_decision = check_plan_limit(
        session,
        workspace_id=workspace_id,
        action=action,
        requested=1,
    )
    if not limit_decision.allowed:
        _create_audit_log(
            session,
            platform="email",
            workspace_id=workspace_id,
            action=action,
            text=body,
            status="blocked_plan",
            error_message="Plan limit exceeded",
            payload={
                "subject": subject,
                "recipients": recipients or [],
                "limit": limit_decision.limit,
                "used": limit_decision.used,
                "requested": 1,
            },
        )
        session.commit()
        return PublishResult(
            workspace_id=workspace_id,
            action=action,
            published=False,
            external_post_id=None,
            status="blocked_plan",
            message="Plan limit exceeded",
        )

    payload = ChannelPayload(
        workspace_id=workspace_id,
        channel="email",
        title=subject,
        body=body,
        metadata={
            "recipients": recipients or [],
            "source_kind": source_kind,
            "source_ref_id": source_ref_id,
        },
    )

    result = publisher.publish(payload)
    if result.published:
        _create_audit_log(
            session,
            platform="email",
            workspace_id=workspace_id,
            action=action,
            text=body,
            status="published",
            external_post_id=result.external_id,
            payload={
                "subject": subject,
                "recipients": recipients or [],
                "provider_payload": result.payload,
            },
        )
        record_usage(
            session,
            workspace_id=workspace_id,
            action=action,
            amount=1,
            payload={"external_post_id": result.external_id, "recipients": recipients or []},
        )
        session.add(
            WorkspaceEvent(
                workspace_id=workspace_id,
                event_type="publish_email",
                payload_json=_json_dumps(
                    {
                        "external_post_id": result.external_id,
                        "source_kind": source_kind,
                        "source_ref_id": source_ref_id,
                    }
                ),
            )
        )
        session.commit()
        return PublishResult(
            workspace_id=workspace_id,
            action=action,
            published=True,
            external_post_id=result.external_id,
            status="published",
            message="Email published",
        )

    record_publish_error(workspace_id=workspace_id, channel="email")
    _create_audit_log(
        session,
        platform="email",
        workspace_id=workspace_id,
        action=action,
        text=body,
        status="failed",
        error_message=result.message,
        payload={
            "subject": subject,
            "recipients": recipients or [],
            "provider_payload": result.payload,
        },
    )
    session.commit()
    return PublishResult(
        workspace_id=workspace_id,
        action=action,
        published=False,
        external_post_id=None,
        status="failed",
        message=result.message,
    )


def publish_blog(
    session: Session,
    *,
    workspace_id: str,
    title: str,
    markdown: str,
    image_url: Optional[str] = None,
    blog_publisher: Optional[BlogPublisher] = None,
    source_kind: Optional[str] = None,
    source_ref_id: Optional[str] = None,
    owner_override: bool = False,
) -> PublishResult:
    action = "publish_blog"
    mode_block = _guard_operational_mode(
        session,
        workspace_id=workspace_id,
        action=action,
        text=markdown,
        platform="blog",
        owner_override=owner_override,
    )
    if mode_block is not None:
        return mode_block
    publisher = blog_publisher or BlogPublisher()

    limit_decision = check_plan_limit(
        session,
        workspace_id=workspace_id,
        action=action,
        requested=1,
    )
    if not limit_decision.allowed:
        _create_audit_log(
            session,
            platform="blog",
            workspace_id=workspace_id,
            action=action,
            text=markdown,
            status="blocked_plan",
            error_message="Plan limit exceeded",
            payload={
                "title": title,
                "image_url": image_url,
                "limit": limit_decision.limit,
                "used": limit_decision.used,
                "requested": 1,
            },
        )
        session.commit()
        return PublishResult(
            workspace_id=workspace_id,
            action=action,
            published=False,
            external_post_id=None,
            status="blocked_plan",
            message="Plan limit exceeded",
        )

    payload = ChannelPayload(
        workspace_id=workspace_id,
        channel="blog",
        title=title,
        body=markdown,
        metadata={
            "source_kind": source_kind,
            "source_ref_id": source_ref_id,
            "image_url": image_url,
        },
    )

    result = publisher.publish(payload)
    if result.published:
        _create_audit_log(
            session,
            platform="blog",
            workspace_id=workspace_id,
            action=action,
            text=markdown,
            status="published",
            external_post_id=result.external_id,
            payload={
                "title": title,
                "image_url": image_url,
                "provider_payload": result.payload,
            },
        )
        record_usage(
            session,
            workspace_id=workspace_id,
            action=action,
            amount=1,
            payload={"external_post_id": result.external_id},
        )
        session.add(
            WorkspaceEvent(
                workspace_id=workspace_id,
                event_type="publish_blog",
                payload_json=_json_dumps(
                    {
                        "external_post_id": result.external_id,
                        "source_kind": source_kind,
                        "source_ref_id": source_ref_id,
                    }
                ),
            )
        )
        session.commit()
        return PublishResult(
            workspace_id=workspace_id,
            action=action,
            published=True,
            external_post_id=result.external_id,
            status="published",
            message="Blog published",
        )

    record_publish_error(workspace_id=workspace_id, channel="blog")
    _create_audit_log(
        session,
        platform="blog",
        workspace_id=workspace_id,
        action=action,
        text=markdown,
        status="failed",
        error_message=result.message,
        payload={
            "title": title,
            "image_url": image_url,
            "provider_payload": result.payload,
        },
    )
    session.commit()
    return PublishResult(
        workspace_id=workspace_id,
        action=action,
        published=False,
        external_post_id=None,
        status="failed",
        message=result.message,
    )


def publish_instagram(
    session: Session,
    *,
    workspace_id: str,
    caption: str,
    image_url: Optional[str] = None,
    instagram_publisher: Optional[InstagramPublisher] = None,
    source_kind: Optional[str] = None,
    source_ref_id: Optional[str] = None,
    scheduled_for: Optional[str] = None,
    owner_override: bool = False,
) -> PublishResult:
    action = "publish_instagram"
    mode_block = _guard_operational_mode(
        session,
        workspace_id=workspace_id,
        action=action,
        text=caption,
        platform="instagram",
        owner_override=owner_override,
    )
    if mode_block is not None:
        return mode_block
    publisher = instagram_publisher or InstagramPublisher()

    limit_decision = check_plan_limit(
        session,
        workspace_id=workspace_id,
        action=action,
        requested=1,
    )
    if not limit_decision.allowed:
        _create_audit_log(
            session,
            platform="instagram",
            workspace_id=workspace_id,
            action=action,
            text=caption,
            status="blocked_plan",
            error_message="Plan limit exceeded",
            payload={
                "image_url": image_url,
                "scheduled_for": scheduled_for,
                "limit": limit_decision.limit,
                "used": limit_decision.used,
                "requested": 1,
            },
        )
        session.commit()
        return PublishResult(
            workspace_id=workspace_id,
            action=action,
            published=False,
            external_post_id=None,
            status="blocked_plan",
            message="Plan limit exceeded",
        )

    payload = ChannelPayload(
        workspace_id=workspace_id,
        channel="instagram",
        title=None,
        body=caption,
        metadata={
            "image_url": image_url,
            "source_kind": source_kind,
            "source_ref_id": source_ref_id,
            "scheduled_for": scheduled_for,
        },
    )

    result = publisher.publish(payload)
    if result.published:
        _create_audit_log(
            session,
            platform="instagram",
            workspace_id=workspace_id,
            action=action,
            text=caption,
            status="published",
            external_post_id=result.external_id,
            payload={
                "image_url": image_url,
                "scheduled_for": scheduled_for,
                "provider_payload": result.payload,
            },
        )
        record_usage(
            session,
            workspace_id=workspace_id,
            action=action,
            amount=1,
            payload={
                "external_post_id": result.external_id,
                "image_url": image_url,
                "scheduled_for": scheduled_for,
            },
        )
        session.add(
            WorkspaceEvent(
                workspace_id=workspace_id,
                event_type="publish_instagram",
                payload_json=_json_dumps(
                    {
                        "external_post_id": result.external_id,
                        "source_kind": source_kind,
                        "source_ref_id": source_ref_id,
                        "scheduled_for": scheduled_for,
                    }
                ),
            )
        )
        session.commit()
        return PublishResult(
            workspace_id=workspace_id,
            action=action,
            published=True,
            external_post_id=result.external_id,
            status="published",
            message="Instagram published",
        )

    record_publish_error(workspace_id=workspace_id, channel="instagram")
    _create_audit_log(
        session,
        platform="instagram",
        workspace_id=workspace_id,
        action=action,
        text=caption,
        status="failed",
        error_message=result.message,
        payload={
            "image_url": image_url,
            "scheduled_for": scheduled_for,
            "provider_payload": result.payload,
        },
    )
    session.commit()
    return PublishResult(
        workspace_id=workspace_id,
        action=action,
        published=False,
        external_post_id=None,
        status="failed",
        message=result.message,
    )
