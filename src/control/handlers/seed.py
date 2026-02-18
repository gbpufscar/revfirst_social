"""Seed command handler."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Dict

from src.control.command_schema import ControlResponse
from src.integrations.telegram.service import extract_style_fingerprint, upsert_telegram_seed

if TYPE_CHECKING:
    from src.control.command_router import CommandContext


def _interpret_seed(text: str) -> Dict[str, str]:
    normalized = text.strip()
    first_sentence = re.split(r"[.!?]", normalized, maxsplit=1)[0].strip()
    hook = first_sentence[:120]

    structure = "statement"
    if "?" in first_sentence:
        structure = "question-led"
    elif first_sentence.lower().startswith(("how", "why", "what")):
        structure = "insight-led"

    cta = "none"
    lowered = normalized.lower()
    if "reply" in lowered or "comment" in lowered:
        cta = "conversation"
    elif "dm" in lowered or "message" in lowered:
        cta = "direct"
    elif "try" in lowered or "signup" in lowered or "link" in lowered:
        cta = "conversion"

    return {
        "hook": hook,
        "structure": structure,
        "cta": cta,
    }


def handle(context: "CommandContext") -> ControlResponse:
    if not context.command.args:
        return ControlResponse(success=False, message="usage: /seed <text>", data={})

    text = context.command.args[0].strip()
    if not text:
        return ControlResponse(success=False, message="seed_text_empty", data={})

    seed = upsert_telegram_seed(
        context.session,
        workspace_id=context.envelope.workspace_id,
        source_chat_id=context.envelope.chat_id,
        source_message_id=context.envelope.message_id,
        source_user_id=context.envelope.telegram_user_id,
        text=text,
    )

    return ControlResponse(
        success=True,
        message="seed_saved",
        data={
            "seed_id": seed.id,
            "workspace_id": seed.workspace_id,
            "style": extract_style_fingerprint(seed.normalized_text),
            "interpretation": _interpret_seed(seed.normalized_text),
        },
    )
