"""Formatting helpers for control-plane command responses."""

from __future__ import annotations

from typing import Any, Dict, Iterable


def _truncate(value: str, *, size: int = 140) -> str:
    normalized = (value or "").strip()
    if len(normalized) <= size:
        return normalized
    return normalized[: size - 3].rstrip() + "..."


def format_queue_item(item: Dict[str, Any]) -> Dict[str, Any]:
    queue_id = str(item.get("queue_id") or item.get("id") or "")
    copy_text = str(item.get("copy") or item.get("content_text") or "")
    image_url = str(item.get("image_url") or "").strip() or None
    return {
        "id": item.get("id"),
        "queue_id": queue_id,
        "type": item.get("item_type"),
        "intent": item.get("intent"),
        "score": item.get("opportunity_score"),
        "copy": copy_text,
        "image_url": image_url,
        "preview": _truncate(copy_text),
        "status": item.get("status"),
    }


def format_recent_errors(items: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    return [
        {
            "source": str(item.get("source") or "unknown"),
            "message": _truncate(str(item.get("message") or ""), size=180),
            "created_at": item.get("created_at"),
        }
        for item in items
    ]


def build_message(*, title: str, lines: list[str]) -> str:
    text_lines = [title]
    text_lines.extend(lines)
    return "\n".join(text_lines)
