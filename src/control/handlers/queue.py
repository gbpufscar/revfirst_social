"""Queue inspection handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.control.command_schema import ControlResponse
from src.control.formatters import format_queue_item
from src.control.services import list_pending_queue_items, parse_queue_metadata

if TYPE_CHECKING:
    from src.control.command_router import CommandContext


def _extract_image_url(metadata: dict[str, object]) -> str | None:
    for key in ("image_url", "media_url", "asset_url"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return None


def handle(context: "CommandContext") -> ControlResponse:
    workspace_id = context.envelope.workspace_id
    items = list_pending_queue_items(context.session, workspace_id=workspace_id, limit=5)

    payload = []
    for item in items:
        metadata = parse_queue_metadata(item)
        image_url = _extract_image_url(metadata)
        payload.append(
            format_queue_item(
                {
                    "id": item.id,
                    "queue_id": item.id,
                    "item_type": item.item_type,
                    "intent": item.intent,
                    "opportunity_score": item.opportunity_score,
                    "content_text": item.content_text,
                    "copy": item.content_text,
                    "image_url": image_url,
                    "status": item.status,
                }
            )
        )

    return ControlResponse(
        success=True,
        message="queue_ok",
        data={
            "workspace_id": workspace_id,
            "count": len(payload),
            "items": payload,
        },
    )
