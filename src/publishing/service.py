"""Publishing engine for channel writers with shared guardrails."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from typing import Any, Dict, Optional
import uuid

from src.channels.base import ChannelPayload
from src.channels.blog.publisher import BlogPublisher
from src.channels.email.publisher import EmailPublisher
from src.channels.instagram.publisher import InstagramPublisher
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.billing.plans import check_plan_limit, record_usage
from src.core.config import get_settings
from src.core.metrics import (
    record_publish_error,
    record_replies_published,
    record_reply_blocked,
)
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
        record_replies_published(workspace_id=workspace_id)
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
) -> PublishResult:
    action = "publish_email"
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
) -> PublishResult:
    action = "publish_blog"
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
) -> PublishResult:
    action = "publish_instagram"
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
