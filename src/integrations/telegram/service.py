"""Workspace-scoped Telegram seed ingestion and style extraction."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any, Dict, Optional, Tuple
import uuid

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from src.storage.models import TelegramSeed


def normalize_seed_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def extract_style_fingerprint(text: str) -> Dict[str, Any]:
    normalized = normalize_seed_text(text)
    sentences = [part.strip() for part in re.split(r"[.!?]+", normalized) if part.strip()]
    words = [word for word in normalized.split(" ") if word]
    sentence_lengths = [len([word for word in sentence.split(" ") if word]) for sentence in sentences]
    average_sentence_words = 0.0
    if sentence_lengths:
        average_sentence_words = sum(sentence_lengths) / len(sentence_lengths)

    opener = ""
    if words:
        opener = " ".join(words[: min(4, len(words))]).lower()

    return {
        "character_count": len(normalized),
        "word_count": len(words),
        "sentence_count": len(sentences),
        "average_sentence_words": round(average_sentence_words, 2),
        "opens_with_question": normalized.endswith("?") if normalized else False,
        "opener": opener,
    }


def _json_dump(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True)


def _coerce_identifier(value: Any, fallback: str) -> str:
    if value is None:
        return fallback
    return str(value)


def upsert_telegram_seed(
    session: Session,
    *,
    workspace_id: str,
    source_chat_id: str,
    source_message_id: str,
    source_user_id: Optional[str],
    text: str,
) -> TelegramSeed:
    normalized_text = normalize_seed_text(text)
    style = extract_style_fingerprint(normalized_text)
    now = datetime.now(timezone.utc)

    existing = session.scalar(
        select(TelegramSeed).where(
            TelegramSeed.workspace_id == workspace_id,
            TelegramSeed.source_chat_id == source_chat_id,
            TelegramSeed.source_message_id == source_message_id,
        )
    )
    if existing is None:
        existing = TelegramSeed(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            source_user_id=source_user_id,
            raw_text=text,
            normalized_text=normalized_text,
            style_fingerprint_json=_json_dump(style),
        )
        session.add(existing)
    else:
        existing.source_user_id = source_user_id
        existing.raw_text = text
        existing.normalized_text = normalized_text
        existing.style_fingerprint_json = _json_dump(style)
        existing.updated_at = now

    session.commit()
    return existing


def ingest_telegram_update(
    session: Session,
    *,
    workspace_id: str,
    update_payload: Dict[str, Any],
    max_text_chars: int = 1200,
) -> Tuple[Optional[TelegramSeed], Optional[str]]:
    message = update_payload.get("message") or update_payload.get("edited_message")
    if not isinstance(message, dict):
        return None, "message_not_found"

    text = message.get("text")
    if not isinstance(text, str):
        return None, "text_not_found"

    normalized = normalize_seed_text(text)
    if not normalized:
        return None, "text_empty"
    if len(normalized) > max_text_chars:
        return None, "text_too_long"

    chat = message.get("chat") or {}
    sender = message.get("from") or {}

    source_chat_id = _coerce_identifier(chat.get("id"), "unknown-chat")
    source_message_id = _coerce_identifier(message.get("message_id"), f"msg-{uuid.uuid4()}")
    source_user_id = _coerce_identifier(sender.get("id"), "unknown-user")

    seed = upsert_telegram_seed(
        session,
        workspace_id=workspace_id,
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
        source_user_id=source_user_id,
        text=normalized,
    )
    return seed, None


def list_recent_telegram_seeds(
    session: Session,
    *,
    workspace_id: str,
    limit: int = 20,
) -> list[TelegramSeed]:
    safe_limit = max(1, min(limit, 100))
    statement = (
        select(TelegramSeed)
        .where(TelegramSeed.workspace_id == workspace_id)
        .order_by(desc(TelegramSeed.created_at))
        .limit(safe_limit)
    )
    return list(session.scalars(statement).all())

