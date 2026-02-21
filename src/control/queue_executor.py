"""Execution helpers for approved scheduled queue items."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import asc, nullsfirst, select
from sqlalchemy.orm import Session

from src.control.services import (
    mark_queue_item_failed,
    mark_queue_item_published,
    mark_queue_item_publishing,
    parse_queue_metadata,
)
from src.editorial.queue_states import APPROVED_SCHEDULED_STATUSES
from src.publishing.service import publish_blog, publish_email, publish_instagram, publish_post, publish_reply
from src.storage.models import ApprovalQueueItem


def _parse_scheduled_for(value: Any) -> datetime | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _item_scheduled_for(item: ApprovalQueueItem) -> datetime | None:
    if item.scheduled_for is not None:
        value = item.scheduled_for
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    metadata = parse_queue_metadata(item)
    return _parse_scheduled_for(metadata.get("scheduled_for"))


def _is_due(item: ApprovalQueueItem, *, now_utc: datetime) -> bool:
    scheduled_for = _item_scheduled_for(item)
    if scheduled_for is None:
        return True
    return scheduled_for <= now_utc


def _publish_single_item(
    session: Session,
    *,
    workspace_id: str,
    item: ApprovalQueueItem,
    x_client: Any,
    owner_override: bool,
) -> tuple[bool, Optional[str], str]:
    metadata = parse_queue_metadata(item)
    try:
        if item.item_type == "reply":
            in_reply_to_tweet_id = str(metadata.get("in_reply_to_tweet_id") or "").strip()
            if not in_reply_to_tweet_id:
                return False, None, "missing_reply_target"
            result = publish_reply(
                session,
                workspace_id=workspace_id,
                text=item.content_text,
                in_reply_to_tweet_id=in_reply_to_tweet_id,
                thread_id=(str(metadata.get("thread_id")) if metadata.get("thread_id") else None),
                target_author_id=(str(metadata.get("target_author_id")) if metadata.get("target_author_id") else None),
                x_client=x_client,
                owner_override=owner_override,
            )
        elif item.item_type == "post":
            result = publish_post(
                session,
                workspace_id=workspace_id,
                text=item.content_text,
                x_client=x_client,
                owner_override=owner_override,
            )
        elif item.item_type == "email":
            recipients_raw = metadata.get("recipients")
            recipients = []
            if isinstance(recipients_raw, str):
                recipients = [value.strip() for value in recipients_raw.split(",") if value.strip()]
            elif isinstance(recipients_raw, list):
                recipients = [str(value).strip() for value in recipients_raw if str(value).strip()]
            result = publish_email(
                session,
                workspace_id=workspace_id,
                subject=str(metadata.get("subject") or "RevFirst update"),
                body=item.content_text,
                recipients=recipients,
                source_kind=item.source_kind,
                source_ref_id=item.source_ref_id,
                owner_override=owner_override,
            )
        elif item.item_type == "blog":
            image_url = str(metadata.get("image_url") or "").strip()
            result = publish_blog(
                session,
                workspace_id=workspace_id,
                title=str(metadata.get("title") or "RevFirst blog draft"),
                markdown=item.content_text,
                image_url=(image_url or None),
                source_kind=item.source_kind,
                source_ref_id=item.source_ref_id,
                owner_override=owner_override,
            )
        elif item.item_type == "instagram":
            image_url = str(
                metadata.get("image_url") or metadata.get("media_url") or metadata.get("asset_url") or ""
            ).strip()
            scheduled_for = _item_scheduled_for(item)
            result = publish_instagram(
                session,
                workspace_id=workspace_id,
                caption=item.content_text,
                image_url=(image_url or None),
                source_kind=item.source_kind,
                source_ref_id=item.source_ref_id,
                scheduled_for=(scheduled_for.isoformat() if scheduled_for is not None else None),
                owner_override=owner_override,
            )
        else:
            return False, None, "unsupported_queue_item_type"
    except Exception:
        return False, None, "unexpected_publish_error"

    if result.published:
        return True, result.external_post_id, result.message
    return False, None, result.message


def execute_approved_queue_items(
    session: Session,
    *,
    workspace_id: str,
    x_client: Any,
    dry_run: bool,
    owner_override: bool,
    now_utc: datetime | None = None,
    due_only: bool = True,
    limit: int = 20,
) -> Dict[str, Any]:
    now = now_utc or datetime.now(timezone.utc)
    items: List[ApprovalQueueItem] = list(
        session.scalars(
            select(ApprovalQueueItem)
            .where(
                ApprovalQueueItem.workspace_id == workspace_id,
                ApprovalQueueItem.status.in_(APPROVED_SCHEDULED_STATUSES),
            )
            .order_by(
                nullsfirst(asc(ApprovalQueueItem.scheduled_for)),
                asc(ApprovalQueueItem.editorial_priority),
                asc(ApprovalQueueItem.created_at),
            )
            .limit(max(1, limit))
        ).all()
    )

    published = 0
    failed = 0
    scheduled_pending = 0

    for item in items:
        if due_only and not _is_due(item, now_utc=now):
            scheduled_pending += 1
            continue

        if dry_run:
            published += 1
            continue

        mark_queue_item_publishing(
            session,
            item=item,
            approved_by_user_id=item.approved_by_user_id,
        )
        ok, external_post_id, message = _publish_single_item(
            session,
            workspace_id=workspace_id,
            item=item,
            x_client=x_client,
            owner_override=owner_override,
        )
        if ok:
            mark_queue_item_published(session, item=item, external_post_id=external_post_id)
            published += 1
        else:
            mark_queue_item_failed(session, item=item, error_message=message or "publish_failed")
            failed += 1

    return {
        "status": "dry_run" if dry_run else "ok",
        "approved_items": len(items),
        "published": published,
        "failed": failed,
        "scheduled_pending": scheduled_pending,
    }


def execute_queue_item_now(
    session: Session,
    *,
    workspace_id: str,
    item: ApprovalQueueItem,
    x_client: Any,
    owner_override: bool,
) -> Dict[str, Any]:
    mark_queue_item_publishing(
        session,
        item=item,
        approved_by_user_id=item.approved_by_user_id,
    )
    ok, external_post_id, message = _publish_single_item(
        session,
        workspace_id=workspace_id,
        item=item,
        x_client=x_client,
        owner_override=owner_override,
    )
    if ok:
        mark_queue_item_published(session, item=item, external_post_id=external_post_id)
        return {
            "published": True,
            "status": "published",
            "external_post_id": external_post_id,
            "message": message,
        }
    mark_queue_item_failed(session, item=item, error_message=message or "publish_failed")
    return {
        "published": False,
        "status": "failed",
        "external_post_id": None,
        "message": message or "publish_failed",
    }
