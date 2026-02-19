"""Queue approve/reject handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.control.command_schema import ControlResponse
from src.control.services import (
    get_queue_item,
    mark_queue_item_approved,
    mark_queue_item_failed,
    mark_queue_item_published,
    mark_queue_item_rejected,
    parse_queue_metadata,
)
from src.publishing.service import publish_post, publish_reply

if TYPE_CHECKING:
    from src.control.command_router import CommandContext


_FINAL_STATUSES = {"published", "rejected", "failed"}


def _require_queue_id(context: "CommandContext") -> str | None:
    if not context.command.args:
        return None
    return str(context.command.args[0]).strip()


def handle(context: "CommandContext") -> ControlResponse:
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

    mark_queue_item_approved(
        context.session,
        item=item,
        approved_by_user_id=context.actor.user_id,
    )

    metadata = parse_queue_metadata(item)
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
        )
    else:
        result = publish_post(
            context.session,
            workspace_id=workspace_id,
            text=item.content_text,
            x_client=context.x_client,
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
