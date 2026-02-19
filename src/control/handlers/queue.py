"""Queue inspection handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.control.command_schema import ControlResponse
from src.control.formatters import format_queue_item
from src.control.services import list_pending_queue_items

if TYPE_CHECKING:
    from src.control.command_router import CommandContext


def handle(context: "CommandContext") -> ControlResponse:
    workspace_id = context.envelope.workspace_id
    items = list_pending_queue_items(context.session, workspace_id=workspace_id, limit=5)

    payload = [
        format_queue_item(
            {
                "id": item.id,
                "item_type": item.item_type,
                "intent": item.intent,
                "opportunity_score": item.opportunity_score,
                "content_text": item.content_text,
                "status": item.status,
            }
        )
        for item in items
    ]

    return ControlResponse(
        success=True,
        message="queue_ok",
        data={
            "workspace_id": workspace_id,
            "count": len(payload),
            "items": payload,
        },
    )
