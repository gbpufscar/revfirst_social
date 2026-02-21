"""Command parsing and shared control-plane contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class TelegramCommandEnvelope:
    workspace_id: str
    update_id: str
    telegram_user_id: str
    chat_id: str
    message_id: str
    text: str


@dataclass(frozen=True)
class ControlCommand:
    name: str
    args: List[str] = field(default_factory=list)
    raw_text: str = ""


@dataclass(frozen=True)
class ControlResponse:
    success: bool
    message: str
    data: Dict[str, Any] = field(default_factory=dict)


def normalize_command_name(raw: str) -> str:
    cleaned = raw.strip().lower()
    if not cleaned.startswith("/"):
        return ""
    token = cleaned[1:]
    if "@" in token:
        token = token.split("@", 1)[0]
    return token


def parse_command(text: str) -> Optional[ControlCommand]:
    stripped = (text or "").strip()
    lowered = stripped.lower()
    if lowered in {"sim", "aprovado", "aprovar", "approve"}:
        return ControlCommand(name="approve_now", args=[], raw_text=stripped)

    if not stripped.startswith("/"):
        return None

    parts = stripped.split(maxsplit=1)
    command_name = normalize_command_name(parts[0])
    remainder = parts[1].strip() if len(parts) > 1 else ""

    if command_name in {
        "help",
        "status",
        "mode",
        "stability",
        "ack_kill_switch",
        "metrics",
        "growth",
        "growth_weekly",
        "queue",
        "pause",
        "resume",
        "daily_report",
        "weekly_report",
        "strategy_report",
        "logs",
    }:
        args = remainder.split() if remainder else []
        return ControlCommand(name=command_name, args=args, raw_text=stripped)

    if command_name in {
        "approve",
        "approve_now",
        "reject",
        "preview",
        "run",
        "channel",
        "limit",
        "strategy_scan",
        "strategy_discover",
    }:
        args = remainder.split() if remainder else []
        return ControlCommand(name=command_name, args=args, raw_text=stripped)

    if command_name == "seed":
        if remainder:
            return ControlCommand(name=command_name, args=[remainder], raw_text=stripped)
        return ControlCommand(name=command_name, args=[], raw_text=stripped)

    return ControlCommand(name="unknown", args=[command_name, remainder] if remainder else [command_name], raw_text=stripped)


def build_idempotency_key(*, update_id: str, command_text: str) -> str:
    source = f"{update_id}:{(command_text or '').strip()}".encode("utf-8")
    digest = hashlib.sha1(source).hexdigest()
    return f"tgcmd-{digest}"


def parse_envelope(*, workspace_id: str, payload: Dict[str, Any]) -> Optional[TelegramCommandEnvelope]:
    message = payload.get("message") or payload.get("edited_message")
    if not isinstance(message, dict):
        return None

    text = message.get("text")
    if not isinstance(text, str) or not text.strip():
        return None

    update_id = str(payload.get("update_id", "unknown-update"))
    sender = message.get("from") or {}
    chat = message.get("chat") or {}

    telegram_user_id = str(sender.get("id", ""))
    chat_id = str(chat.get("id", "unknown-chat"))
    message_id = str(message.get("message_id", "unknown-message"))

    if not telegram_user_id:
        return None

    return TelegramCommandEnvelope(
        workspace_id=workspace_id,
        update_id=update_id,
        telegram_user_id=telegram_user_id,
        chat_id=chat_id,
        message_id=message_id,
        text=text,
    )
