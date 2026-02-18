"""Telegram command-center webhook routes."""

from __future__ import annotations

from time import perf_counter
from uuid import uuid4
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from src.control.command_router import CommandContext, dispatch_command
from src.control.command_schema import build_idempotency_key, parse_command, parse_envelope
from src.control.security import ControlAuthorizationError, resolve_control_actor
from src.control.services import create_admin_action
from src.core.config import get_settings
from src.integrations.x.x_client import XClient, get_x_client
from src.schemas.control import ControlWebhookResponse
from src.storage.db import get_session
from src.storage.tenant import set_workspace_context
from src.storage.redis_client import get_client as get_redis_client


router = APIRouter(prefix="/control/telegram", tags=["control-telegram"])


def _verify_webhook_secret(received_secret: str | None) -> None:
    settings = get_settings()
    configured = settings.telegram_webhook_secret.strip()
    if not configured:
        return
    if received_secret != configured:
        raise ControlAuthorizationError("invalid_telegram_webhook_secret")


@router.post("/webhook/{workspace_id}", response_model=ControlWebhookResponse)
def control_webhook(
    workspace_id: str,
    payload: Dict[str, Any],
    session: Session = Depends(get_session),
    x_client: XClient = Depends(get_x_client),
    telegram_secret_token: Optional[str] = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
) -> ControlWebhookResponse:
    request_id = f"ctl-{uuid4()}"
    started_at = perf_counter()

    set_workspace_context(session, workspace_id)

    envelope = parse_envelope(workspace_id=workspace_id, payload=payload)
    if envelope is None:
        return ControlWebhookResponse(
            accepted=False,
            workspace_id=workspace_id,
            request_id=request_id,
            command=None,
            status="ignored",
            message="message_not_supported",
        )

    command = parse_command(envelope.text)
    if command is None:
        return ControlWebhookResponse(
            accepted=False,
            workspace_id=workspace_id,
            request_id=request_id,
            command=None,
            status="ignored",
            message="message_is_not_command",
        )

    idempotency_key = build_idempotency_key(update_id=envelope.update_id, command_text=command.raw_text)

    try:
        _verify_webhook_secret(telegram_secret_token)
        actor = resolve_control_actor(
            session,
            workspace_id=workspace_id,
            telegram_user_id=envelope.telegram_user_id,
        )

        context = CommandContext(
            session=session,
            redis_client=get_redis_client(),
            x_client=x_client,
            envelope=envelope,
            command=command,
            actor=actor,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
        response = dispatch_command(context)

        duration_ms = int((perf_counter() - started_at) * 1000)
        create_admin_action(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor.user_id,
            telegram_user_id=envelope.telegram_user_id,
            command=command.name,
            payload={"args": command.args, "text": command.raw_text},
            status="success" if response.success else "error",
            result_summary=response.message,
            error_message=None if response.success else response.message,
            duration_ms=duration_ms,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )

        return ControlWebhookResponse(
            accepted=response.success,
            workspace_id=workspace_id,
            request_id=request_id,
            command=command.name,
            status="ok" if response.success else "error",
            message=response.message,
            data=response.data,
        )
    except ControlAuthorizationError as exc:
        duration_ms = int((perf_counter() - started_at) * 1000)
        create_admin_action(
            session,
            workspace_id=workspace_id,
            actor_user_id=None,
            telegram_user_id=envelope.telegram_user_id,
            command=command.name,
            payload={"args": command.args, "text": command.raw_text},
            status="unauthorized",
            result_summary="unauthorized",
            error_message=str(exc),
            duration_ms=duration_ms,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
        return ControlWebhookResponse(
            accepted=False,
            workspace_id=workspace_id,
            request_id=request_id,
            command=command.name,
            status="unauthorized",
            message="unauthorized",
            data={"reason": str(exc)},
        )
    except Exception as exc:
        duration_ms = int((perf_counter() - started_at) * 1000)
        create_admin_action(
            session,
            workspace_id=workspace_id,
            actor_user_id=None,
            telegram_user_id=envelope.telegram_user_id,
            command=command.name,
            payload={"args": command.args, "text": command.raw_text},
            status="error",
            result_summary="execution_error",
            error_message=str(exc),
            duration_ms=duration_ms,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
        return ControlWebhookResponse(
            accepted=False,
            workspace_id=workspace_id,
            request_id=request_id,
            command=command.name,
            status="error",
            message="execution_error",
            data={"error": str(exc)},
        )
