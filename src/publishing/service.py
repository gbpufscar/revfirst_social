"""Single publishing engine (only X write path)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from typing import Any, Dict, Optional
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.billing.plans import check_plan_limit, record_usage
from src.core.config import get_settings
from src.integrations.x.service import get_workspace_x_access_token
from src.integrations.x.x_client import XClient, XClientError
from src.storage.models import PublishAuditLog, PublishCooldown, WorkspaceEvent


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
        platform="x",
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


def publish_reply(
    session: Session,
    *,
    workspace_id: str,
    text: str,
    in_reply_to_tweet_id: str,
    thread_id: Optional[str],
    target_author_id: Optional[str],
    x_client: XClient,
) -> PublishResult:
    settings = get_settings()
    action = "publish_reply"

    token = get_workspace_x_access_token(session, workspace_id=workspace_id)
    if token is None:
        _create_audit_log(
            session,
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
        _create_audit_log(
            session,
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
        _create_audit_log(
            session,
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
        _create_audit_log(
            session,
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
) -> PublishResult:
    action = "publish_post"
    token = get_workspace_x_access_token(session, workspace_id=workspace_id)
    if token is None:
        _create_audit_log(
            session,
            workspace_id=workspace_id,
            action=action,
            text=text,
            status="failed",
            error_message="Workspace X OAuth is missing or expired",
        )
        session.commit()
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
        _create_audit_log(
            session,
            workspace_id=workspace_id,
            action=action,
            text=text,
            status="failed",
            error_message=str(exc),
        )
        session.commit()
        return PublishResult(
            workspace_id=workspace_id,
            action=action,
            published=False,
            external_post_id=None,
            status="failed",
            message="X publish failed",
        )

