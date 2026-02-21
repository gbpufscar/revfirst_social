"""Queue approve/reject handlers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from src.control.command_schema import ControlResponse
from src.control.services import (
    get_queue_item,
    list_pending_queue_items,
    mark_queue_item_approved,
    mark_queue_item_failed,
    mark_queue_item_publishing,
    mark_queue_item_published,
    mark_queue_item_rejected,
    parse_queue_metadata,
)
from src.publishing.service import (
    publish_blog,
    publish_email,
    publish_instagram,
    publish_post,
    publish_reply,
)

if TYPE_CHECKING:
    from src.control.command_router import CommandContext


_FINAL_STATUSES = {"published", "rejected", "failed"}


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


def _parse_scheduled_for(metadata: dict[str, object]) -> datetime | None:
    raw_value = metadata.get("scheduled_for")
    if raw_value is None:
        return None
    value = str(raw_value).strip()
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def handle(context: "CommandContext") -> ControlResponse:
    workspace_id = context.envelope.workspace_id
    owner_override = _owner_override_requested(context)
    queue_id = _require_queue_id(context)
    if queue_id:
        item = get_queue_item(context.session, workspace_id=workspace_id, queue_item_id=queue_id)
    else:
        latest_pending = list_pending_queue_items(context.session, workspace_id=workspace_id, limit=1)
        item = latest_pending[0] if latest_pending else None
        queue_id = item.id if item is not None else None

    if queue_id is None:
        return ControlResponse(success=False, message="no_pending_queue_item", data={})

    if item is None:
        return ControlResponse(success=False, message="queue_item_not_found", data={"queue_id": queue_id})

    if item.status in _FINAL_STATUSES:
        return ControlResponse(
            success=True,
            message="approve_idempotent",
            data={
                "queue_id": queue_id,
                "status": item.status,
                "published_post_id": item.published_post_id,
            },
        )

    if item.status == "approved":
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
            data={
                "queue_id": queue_id,
                "status": item.status,
            },
        )

    metadata = parse_queue_metadata(item)
    if item.item_type == "instagram":
        scheduled_for = _parse_scheduled_for(metadata)
        if scheduled_for is not None and scheduled_for > datetime.now(timezone.utc):
            mark_queue_item_approved(
                context.session,
                item=item,
                approved_by_user_id=context.actor.user_id,
            )
            return ControlResponse(
                success=True,
                message="approved_scheduled",
                data={
                    "queue_id": queue_id,
                    "status": "approved",
                    "scheduled_for": scheduled_for.isoformat(),
                },
            )
    elif item.item_type not in {"reply", "post", "email", "blog", "instagram"}:
        mark_queue_item_failed(context.session, item=item, error_message="unsupported_queue_item_type")
        return ControlResponse(
            success=False,
            message="unsupported_queue_item_type",
            data={"queue_id": queue_id, "status": "failed"},
        )

    mark_queue_item_publishing(
        context.session,
        item=item,
        approved_by_user_id=context.actor.user_id,
    )

    try:
        if item.item_type == "reply":
            in_reply_to_tweet_id = str(metadata.get("in_reply_to_tweet_id") or "").strip()
            if not in_reply_to_tweet_id:
                mark_queue_item_failed(context.session, item=item, error_message="missing_reply_target")
                return ControlResponse(
                    success=False,
                    message="missing_reply_target",
                    data={"queue_id": queue_id, "status": "failed"},
                )

            result = publish_reply(
                context.session,
                workspace_id=workspace_id,
                text=item.content_text,
                in_reply_to_tweet_id=in_reply_to_tweet_id,
                thread_id=(str(metadata.get("thread_id")) if metadata.get("thread_id") else None),
                target_author_id=(str(metadata.get("target_author_id")) if metadata.get("target_author_id") else None),
                x_client=context.x_client,
                owner_override=owner_override,
            )
        elif item.item_type == "post":
            result = publish_post(
                context.session,
                workspace_id=workspace_id,
                text=item.content_text,
                x_client=context.x_client,
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
                context.session,
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
                context.session,
                workspace_id=workspace_id,
                title=str(metadata.get("title") or "RevFirst blog draft"),
                markdown=item.content_text,
                image_url=(image_url or None),
                source_kind=item.source_kind,
                source_ref_id=item.source_ref_id,
                owner_override=owner_override,
            )
        else:
            scheduled_for = _parse_scheduled_for(metadata)
            image_url = str(
                metadata.get("image_url") or metadata.get("media_url") or metadata.get("asset_url") or ""
            ).strip()
            result = publish_instagram(
                context.session,
                workspace_id=workspace_id,
                caption=item.content_text,
                image_url=(image_url or None),
                source_kind=item.source_kind,
                source_ref_id=item.source_ref_id,
                scheduled_for=(scheduled_for.isoformat() if scheduled_for is not None else None),
                owner_override=owner_override,
            )
    except Exception:
        mark_queue_item_failed(context.session, item=item, error_message="unexpected_publish_error")
        return ControlResponse(
            success=False,
            message="approve_publish_failed",
            data={
                "queue_id": queue_id,
                "status": "failed",
                "publish_status": "failed",
                "error": "unexpected_publish_error",
            },
        )

    if result.published:
        mark_queue_item_published(context.session, item=item, external_post_id=result.external_post_id)
        return ControlResponse(
            success=True,
            message="approved_and_published",
            data={
                "queue_id": queue_id,
                "status": "published",
                "external_post_id": result.external_post_id,
            },
        )

    mark_queue_item_failed(context.session, item=item, error_message=result.message)
    return ControlResponse(
        success=False,
        message="approve_publish_failed",
        data={
            "queue_id": queue_id,
            "status": "failed",
            "publish_status": result.status,
            "error": result.message,
        },
    )


def handle_reject(context: "CommandContext") -> ControlResponse:
    workspace_id = context.envelope.workspace_id
    queue_id = _require_queue_id(context)
    if not queue_id:
        return ControlResponse(success=False, message="missing_queue_id", data={})

    item = get_queue_item(context.session, workspace_id=workspace_id, queue_item_id=queue_id)
    if item is None:
        return ControlResponse(success=False, message="queue_item_not_found", data={"queue_id": queue_id})

    if item.status in _FINAL_STATUSES:
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
    return ControlResponse(
        success=True,
        message="queue_item_rejected",
        data={"queue_id": queue_id, "status": "rejected"},
    )
