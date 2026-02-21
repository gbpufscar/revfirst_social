"""Queue preview handler with image and copy payload for chat rendering."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.control.command_schema import ControlResponse
from src.control.services import QueueItemLookupError, get_queue_item, parse_queue_metadata

if TYPE_CHECKING:
    from src.control.command_router import CommandContext


def _extract_image_url(metadata: dict[str, object]) -> str | None:
    for key in ("image_url", "media_url", "asset_url"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return None


def _build_caption(*, queue_id: str, item_type: str, copy_text: str) -> str:
    normalized_copy = " ".join((copy_text or "").split())
    truncated_copy = normalized_copy[:900]
    if len(normalized_copy) > len(truncated_copy):
        truncated_copy += "..."
    return (
        f"Preview {item_type.upper()} | queue_id: {queue_id}\n\n"
        f"{truncated_copy}\n\n"
        f"Para publicar: /approve {queue_id}"
    )


def handle(context: "CommandContext") -> ControlResponse:
    workspace_id = context.envelope.workspace_id
    if not context.command.args:
        return ControlResponse(success=False, message="missing_queue_id", data={})

    queue_id = str(context.command.args[0]).strip()
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

    metadata = parse_queue_metadata(item)
    image_url = _extract_image_url(metadata)
    copy_text = item.content_text

    data = {
        "queue_id": queue_id,
        "item_type": item.item_type,
        "status": item.status,
        "copy": copy_text,
        "image_url": image_url,
    }

    if image_url:
        data["preview_photo"] = {
            "image_url": image_url,
            "caption": _build_caption(queue_id=queue_id, item_type=item.item_type, copy_text=copy_text),
        }
        return ControlResponse(success=True, message="preview_ready", data=data)

    return ControlResponse(success=True, message="preview_image_unavailable", data=data)
