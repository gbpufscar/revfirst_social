"""Queue approve/reject handlers."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Dict

from sqlalchemy import func, select

from src.control.command_schema import ControlResponse
from src.control.queue_executor import execute_queue_item_now
from src.control.services import (
    QueueItemLookupError,
    create_queue_item,
    get_queue_item,
    list_pending_queue_items,
    mark_queue_item_failed,
    mark_queue_item_rejected,
    parse_queue_metadata,
    schedule_queue_item_for_next_window,
)
from src.control.state import acquire_pipeline_run_lock
from src.core.config import get_settings
from src.daily_post.service import generate_daily_post
from src.editorial.queue_states import (
    APPROVED_SCHEDULED_STATUSES,
    FINAL_QUEUE_STATUSES,
    PENDING_REVIEW_STATUSES,
    QUEUE_STATUS_APPROVED_SCHEDULED,
)
from src.media.service import generate_image_asset
from src.storage.models import ApprovalQueueItem, WorkspaceEvent

if TYPE_CHECKING:
    from src.control.command_router import CommandContext


_SUPPORTED_QUEUE_TYPES = {"reply", "post", "email", "blog", "instagram"}
_REJECT_REGEN_WINDOW_MINUTES = 60


def _require_queue_id(context: "CommandContext") -> str | None:
    if not context.command.args:
        return None
    return str(context.command.args[0]).strip()


def _owner_override_requested(context: "CommandContext") -> bool:
    if context.actor.role != "owner":
        return False
    if len(context.command.args) < 2:
        return False
    flags = {str(value).strip().lower() for value in context.command.args[1:] if str(value).strip()}
    return bool(flags.intersection({"override", "--override", "owner_override=true"}))


def _resolve_queue_item(
    context: "CommandContext",
) -> tuple[ApprovalQueueItem | None, str | None, Dict[str, Any] | None]:
    workspace_id = context.envelope.workspace_id
    queue_id = _require_queue_id(context)
    if queue_id:
        try:
            item = get_queue_item(context.session, workspace_id=workspace_id, queue_item_id=queue_id)
        except QueueItemLookupError as exc:
            return None, queue_id, {"message": "queue_id_ambiguous", "candidates": exc.candidates}
        return item, queue_id, None

    latest_pending = list_pending_queue_items(context.session, workspace_id=workspace_id, limit=1)
    item = latest_pending[0] if latest_pending else None
    return item, (item.id if item is not None else None), None


def _scheduled_payload(item: ApprovalQueueItem) -> Dict[str, Any]:
    scheduled_for = item.scheduled_for
    if scheduled_for is not None and scheduled_for.tzinfo is None:
        scheduled_for = scheduled_for.replace(tzinfo=timezone.utc)
    metadata = parse_queue_metadata(item)
    return {
        "queue_id": item.id,
        "status": item.status,
        "scheduled_for": (
            scheduled_for.isoformat()
            if scheduled_for is not None
            else str(metadata.get("scheduled_for") or "")
        ),
        "window_key": item.publish_window_key or str(metadata.get("publish_window_key") or ""),
    }


def _handle_schedule(context: "CommandContext") -> ControlResponse:
    item, queue_id, lookup_error = _resolve_queue_item(context)
    if queue_id is None:
        return ControlResponse(success=False, message="no_pending_queue_item", data={})
    if lookup_error is not None:
        return ControlResponse(
            success=False,
            message=str(lookup_error["message"]),
            data={"queue_id": queue_id, "candidates": lookup_error.get("candidates") or []},
        )
    if item is None:
        return ControlResponse(success=False, message="queue_item_not_found", data={"queue_id": queue_id})

    if item.status in FINAL_QUEUE_STATUSES:
        return ControlResponse(
            success=True,
            message="approve_idempotent",
            data={
                "queue_id": queue_id,
                "status": item.status,
                "published_post_id": item.published_post_id,
            },
        )
    if item.status in APPROVED_SCHEDULED_STATUSES:
        return ControlResponse(success=True, message="approved_scheduled", data=_scheduled_payload(item))
    if item.status == "publishing":
        return ControlResponse(
            success=True,
            message="approve_in_progress",
            data={"queue_id": queue_id, "status": item.status},
        )

    if item.item_type not in _SUPPORTED_QUEUE_TYPES:
        mark_queue_item_failed(context.session, item=item, error_message="unsupported_queue_item_type")
        return ControlResponse(
            success=False,
            message="unsupported_queue_item_type",
            data={"queue_id": queue_id, "status": "failed"},
        )

    updated = schedule_queue_item_for_next_window(
        context.session,
        item=item,
        approved_by_user_id=context.actor.user_id,
    )
    return ControlResponse(
        success=True,
        message="approved_scheduled",
        data=_scheduled_payload(updated),
    )


def _handle_publish_now(context: "CommandContext") -> ControlResponse:
    workspace_id = context.envelope.workspace_id
    owner_override = _owner_override_requested(context)

    item, queue_id, lookup_error = _resolve_queue_item(context)
    if queue_id is None:
        return ControlResponse(success=False, message="no_pending_queue_item", data={})
    if lookup_error is not None:
        return ControlResponse(
            success=False,
            message=str(lookup_error["message"]),
            data={"queue_id": queue_id, "candidates": lookup_error.get("candidates") or []},
        )
    if item is None:
        return ControlResponse(success=False, message="queue_item_not_found", data={"queue_id": queue_id})

    if item.status in FINAL_QUEUE_STATUSES:
        return ControlResponse(
            success=True,
            message="approve_idempotent",
            data={
                "queue_id": queue_id,
                "status": item.status,
                "published_post_id": item.published_post_id,
            },
        )
    if item.status == "publishing":
        return ControlResponse(
            success=True,
            message="approve_in_progress",
            data={"queue_id": queue_id, "status": item.status},
        )

    if item.status in PENDING_REVIEW_STATUSES:
        # /approve_now keeps day-1 compatibility: implicit immediate publish path.
        item.status = QUEUE_STATUS_APPROVED_SCHEDULED
        item.approved_by_user_id = context.actor.user_id
        item.approved_at = datetime.now(timezone.utc)
        context.session.commit()

    result = execute_queue_item_now(
        context.session,
        workspace_id=workspace_id,
        item=item,
        x_client=context.x_client,
        owner_override=owner_override,
    )

    if result["published"]:
        return ControlResponse(
            success=True,
            message="approved_and_published",
            data={
                "queue_id": queue_id,
                "status": "published",
                "external_post_id": result["external_post_id"],
            },
        )

    return ControlResponse(
        success=False,
        message="approve_publish_failed",
        data={
            "queue_id": queue_id,
            "status": "failed",
            "publish_status": result["status"],
            "error": result["message"],
        },
    )


def _count_post_stock_today(context: "CommandContext", *, now: datetime) -> int:
    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    return int(
        context.session.scalar(
            select(func.count())
            .select_from(ApprovalQueueItem)
            .where(
                ApprovalQueueItem.workspace_id == context.envelope.workspace_id,
                ApprovalQueueItem.item_type == "post",
                ApprovalQueueItem.status.in_(PENDING_REVIEW_STATUSES + APPROVED_SCHEDULED_STATUSES),
                ApprovalQueueItem.created_at >= day_start,
            )
        )
        or 0
    )


def _count_pending_review(context: "CommandContext") -> int:
    return int(
        context.session.scalar(
            select(func.count())
            .select_from(ApprovalQueueItem)
            .where(
                ApprovalQueueItem.workspace_id == context.envelope.workspace_id,
                ApprovalQueueItem.item_type == "post",
                ApprovalQueueItem.status.in_(PENDING_REVIEW_STATUSES),
            )
        )
        or 0
    )


def _count_regen_today(context: "CommandContext", *, now: datetime) -> int:
    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    return int(
        context.session.scalar(
            select(func.count())
            .select_from(WorkspaceEvent)
            .where(
                WorkspaceEvent.workspace_id == context.envelope.workspace_id,
                WorkspaceEvent.event_type == "editorial_auto_regeneration_created",
                WorkspaceEvent.created_at >= day_start,
            )
        )
        or 0
    )


def _count_recent_rejects(context: "CommandContext", *, now: datetime) -> int:
    cutoff = now - timedelta(minutes=_REJECT_REGEN_WINDOW_MINUTES)
    return int(
        context.session.scalar(
            select(func.count())
            .select_from(ApprovalQueueItem)
            .where(
                ApprovalQueueItem.workspace_id == context.envelope.workspace_id,
                ApprovalQueueItem.item_type == "post",
                ApprovalQueueItem.source_kind == "daily_post_draft",
                ApprovalQueueItem.status == "rejected",
                ApprovalQueueItem.rejected_at >= cutoff,
            )
        )
        or 0
    )


def _attempt_auto_regeneration(context: "CommandContext", *, rejected_item: ApprovalQueueItem) -> Dict[str, Any]:
    if rejected_item.item_type != "post" or rejected_item.source_kind != "daily_post_draft":
        return {"triggered": False, "reason": "not_daily_post_item"}

    settings = get_settings()
    now = datetime.now(timezone.utc)

    stock_today = _count_post_stock_today(context, now=now)
    if stock_today >= max(1, settings.posts_per_day_target):
        return {"triggered": False, "reason": "stock_target_reached", "stock_today": stock_today}

    pending_review_count = _count_pending_review(context)
    if pending_review_count >= max(1, settings.max_pending_review):
        return {
            "triggered": False,
            "reason": "pending_review_cap_reached",
            "pending_review_count": pending_review_count,
        }

    regen_count_today = _count_regen_today(context, now=now)
    if regen_count_today >= max(1, settings.max_regen_per_day):
        return {"triggered": False, "reason": "regen_cap_reached", "regen_count_today": regen_count_today}

    recent_rejects = _count_recent_rejects(context, now=now)
    if recent_rejects >= max(1, settings.max_regen_per_day):
        return {"triggered": False, "reason": "reject_burst_detected", "recent_rejects": recent_rejects}

    lock = acquire_pipeline_run_lock(
        context.redis_client,
        workspace_id=context.envelope.workspace_id,
        pipeline="editorial_regen",
        ttl_seconds=max(30, settings.control_run_lock_ttl_seconds),
    )
    if lock is None:
        return {"triggered": False, "reason": "regen_lock_unavailable"}

    try:
        generated = generate_daily_post(
            context.session,
            workspace_id=context.envelope.workspace_id,
            topic=None,
            auto_publish=False,
            x_client=context.x_client,
        )
        if generated.status != "ready" or "x" not in set(generated.channel_targets):
            context.session.add(
                WorkspaceEvent(
                    workspace_id=context.envelope.workspace_id,
                    event_type="editorial_auto_regeneration_skipped",
                    payload_json=json.dumps(
                        {
                            "reason": "generation_not_ready",
                            "draft_id": generated.draft_id,
                            "status": generated.status,
                        },
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ),
                )
            )
            context.session.commit()
            return {
                "triggered": False,
                "reason": "generation_not_ready",
                "draft_id": generated.draft_id,
                "draft_status": generated.status,
            }

        preview = (generated.channel_previews or {}).get("x") if isinstance(generated.channel_previews, dict) else {}
        preview_metadata = preview.get("metadata") if isinstance(preview, dict) else {}
        metadata = preview_metadata if isinstance(preview_metadata, dict) else {}
        image_url = str(metadata.get("image_url") or "").strip() or None
        media_asset_id = str(metadata.get("media_asset_id") or "").strip() or None

        if not image_url:
            media_result = generate_image_asset(
                context.session,
                workspace_id=context.envelope.workspace_id,
                channel="x",
                content_text=generated.text,
                source_kind="daily_post_draft",
                source_ref_id=generated.draft_id,
                idempotency_key=f"daily_post_media:x:{generated.draft_id}",
                metadata={"draft_id": generated.draft_id, "content_type": "short_post"},
            )
            if media_result.success and media_result.public_url:
                image_url = media_result.public_url
                media_asset_id = media_result.asset_id

        queue_item = create_queue_item(
            context.session,
            workspace_id=context.envelope.workspace_id,
            item_type="post",
            content_text=generated.text,
            source_kind="daily_post_draft",
            source_ref_id=generated.draft_id,
            intent="daily_post",
            opportunity_score=100,
            idempotency_key=f"daily_post_regen:{generated.draft_id}",
            metadata={
                "draft_id": generated.draft_id,
                "image_url": image_url,
                "media_asset_id": media_asset_id,
                "regenerated_from_queue_id": rejected_item.id,
            },
        )

        context.session.add(
            WorkspaceEvent(
                workspace_id=context.envelope.workspace_id,
                event_type="editorial_auto_regeneration_created",
                payload_json=json.dumps(
                    {
                        "queue_id": queue_item.id,
                        "draft_id": generated.draft_id,
                        "rejected_queue_id": rejected_item.id,
                    },
                    separators=(",", ":"),
                    ensure_ascii=True,
                ),
            )
        )
        context.session.commit()

        return {
            "triggered": True,
            "reason": "ok",
            "draft_id": generated.draft_id,
            "queue_id": queue_item.id,
        }
    finally:
        lock.release()


def handle(context: "CommandContext") -> ControlResponse:
    return _handle_schedule(context)


def handle_now(context: "CommandContext") -> ControlResponse:
    return _handle_publish_now(context)


def handle_reject(context: "CommandContext") -> ControlResponse:
    workspace_id = context.envelope.workspace_id
    queue_id = _require_queue_id(context)
    if not queue_id:
        return ControlResponse(success=False, message="missing_queue_id", data={})

    try:
        item = get_queue_item(context.session, workspace_id=workspace_id, queue_item_id=queue_id)
    except QueueItemLookupError as exc:
        return ControlResponse(
            success=False,
            message="queue_id_ambiguous",
            data={"queue_id": queue_id, "candidates": exc.candidates},
        )
    if item is None:
        return ControlResponse(success=False, message="queue_item_not_found", data={"queue_id": queue_id})

    if item.status in FINAL_QUEUE_STATUSES:
        return ControlResponse(
            success=True,
            message="reject_idempotent",
            data={"queue_id": queue_id, "status": item.status},
        )

    mark_queue_item_rejected(
        context.session,
        item=item,
        rejected_by_user_id=context.actor.user_id,
    )

    regen = _attempt_auto_regeneration(context, rejected_item=item)
    response_data = {"queue_id": queue_id, "status": "rejected", "auto_regeneration": regen}
    if regen.get("triggered"):
        return ControlResponse(success=True, message="queue_item_rejected_regenerated", data=response_data)
    return ControlResponse(success=True, message="queue_item_rejected", data=response_data)
