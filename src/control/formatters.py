"""Formatting helpers for control-plane command responses."""

from __future__ import annotations

from typing import Any, Dict, Iterable


def _truncate(value: str, *, size: int = 140) -> str:
    normalized = (value or "").strip()
    if len(normalized) <= size:
        return normalized
    return normalized[: size - 3].rstrip() + "..."


def format_queue_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": item.get("id"),
        "type": item.get("item_type"),
        "intent": item.get("intent"),
        "score": item.get("opportunity_score"),
        "preview": _truncate(str(item.get("content_text") or "")),
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
