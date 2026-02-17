"""Telegram seed ingestion interface."""

from __future__ import annotations

from typing import Any


class TelegramBot:
    def parse_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Normalize Telegram update payload into seed event format."""
        message = payload.get("message", {})
        return {
            "chat_id": message.get("chat", {}).get("id"),
            "user_id": message.get("from", {}).get("id"),
            "text": message.get("text", ""),
            "date": message.get("date"),
        }
