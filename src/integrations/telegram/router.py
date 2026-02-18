"""Telegram integration routes for content seeding."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from src.auth.dependencies import require_workspace_role
from src.auth.jwt import AuthContext
from src.core.config import get_settings
from src.integrations.telegram.service import ingest_telegram_update, list_recent_telegram_seeds, upsert_telegram_seed
from src.schemas.integrations_telegram import TelegramManualSeedRequest, TelegramSeedResponse, TelegramWebhookResponse
from src.storage.db import get_session
from src.storage.tenant import set_workspace_context


router = APIRouter(prefix="/integrations/telegram", tags=["integrations-telegram"])


def _enforce_workspace_scope(auth: AuthContext, workspace_id: str) -> None:
    if auth.workspace_id != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token workspace scope mismatch",
        )


def _decode_style(value: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    except Exception:  # pragma: no cover
        pass
    return {}


def _verify_webhook_secret(received_secret: str | None) -> None:
    settings = get_settings()
    configured = settings.telegram_webhook_secret.strip()
    if not configured:
        return
    if received_secret != configured:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Telegram webhook secret")


@router.post("/webhook/{workspace_id}", response_model=TelegramWebhookResponse)
def telegram_webhook(
    workspace_id: str,
    payload: Dict[str, Any],
    session: Session = Depends(get_session),
    telegram_secret_token: Optional[str] = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
) -> TelegramWebhookResponse:
    _verify_webhook_secret(telegram_secret_token)
    set_workspace_context(session, workspace_id)

    settings = get_settings()
    seed, reason = ingest_telegram_update(
        session,
        workspace_id=workspace_id,
        update_payload=payload,
        max_text_chars=settings.telegram_seed_max_text_chars,
    )
    if seed is None:
        return TelegramWebhookResponse(
            accepted=False,
            workspace_id=workspace_id,
            reason=reason or "ignored",
        )

    return TelegramWebhookResponse(
        accepted=True,
        workspace_id=workspace_id,
        seed_id=seed.id,
    )


@router.post("/seed/manual", response_model=TelegramSeedResponse)
def manual_seed(
    payload: TelegramManualSeedRequest,
    auth: AuthContext = Depends(require_workspace_role("owner", "admin", "member")),
    session: Session = Depends(get_session),
) -> TelegramSeedResponse:
    _enforce_workspace_scope(auth, payload.workspace_id)
    set_workspace_context(session, payload.workspace_id)
    source_message_id = payload.source_message_id or f"manual-{payload.workspace_id[:8]}"

    seed = upsert_telegram_seed(
        session,
        workspace_id=payload.workspace_id,
        source_chat_id=payload.source_chat_id,
        source_message_id=source_message_id,
        source_user_id=payload.source_user_id,
        text=payload.text,
    )
    return TelegramSeedResponse(
        seed_id=seed.id,
        workspace_id=seed.workspace_id,
        source_chat_id=seed.source_chat_id,
        source_message_id=seed.source_message_id,
        source_user_id=seed.source_user_id,
        text=seed.normalized_text,
        style_fingerprint=_decode_style(seed.style_fingerprint_json),
        created_at=seed.created_at,
    )


@router.get("/seeds/{workspace_id}", response_model=list[TelegramSeedResponse])
def list_seeds(
    workspace_id: str,
    limit: int = 20,
    auth: AuthContext = Depends(require_workspace_role("owner", "admin", "member")),
    session: Session = Depends(get_session),
) -> list[TelegramSeedResponse]:
    _enforce_workspace_scope(auth, workspace_id)
    set_workspace_context(session, workspace_id)

    seeds = list_recent_telegram_seeds(
        session,
        workspace_id=workspace_id,
        limit=limit,
    )
    response: list[TelegramSeedResponse] = []
    for seed in seeds:
        response.append(
            TelegramSeedResponse(
                seed_id=seed.id,
                workspace_id=seed.workspace_id,
                source_chat_id=seed.source_chat_id,
                source_message_id=seed.source_message_id,
                source_user_id=seed.source_user_id,
                text=seed.normalized_text,
                style_fingerprint=_decode_style(seed.style_fingerprint_json),
                created_at=seed.created_at,
            )
        )
    return response
