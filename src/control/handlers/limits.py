"""Plan limit override handler."""

from __future__ import annotations

from datetime import timezone
from typing import TYPE_CHECKING

from src.control.command_schema import ControlResponse
from src.control.services import set_limit_override
from src.core.config import get_settings

if TYPE_CHECKING:
    from src.control.command_router import CommandContext


def handle(context: "CommandContext") -> ControlResponse:
    if len(context.command.args) < 2:
        return ControlResponse(success=False, message="usage: /limit replies|posts <n>", data={})

    kind = context.command.args[0].strip().lower()
    raw_value = context.command.args[1].strip()
    try:
        value = int(raw_value)
    except ValueError:
        return ControlResponse(success=False, message="invalid_limit_value", data={"value": raw_value})

    settings = get_settings()
    try:
        control = set_limit_override(
            context.session,
            workspace_id=context.envelope.workspace_id,
            kind=kind,
            value=value,
            ttl_seconds=settings.control_limit_override_ttl_seconds,
        )
    except ValueError as exc:
        return ControlResponse(success=False, message=str(exc), data={})

    expires_at = control.limit_override_expires_at
    expires_iso = None
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        expires_iso = expires_at.isoformat()

    return ControlResponse(
        success=True,
        message="limit_override_updated",
        data={
            "workspace_id": context.envelope.workspace_id,
            "kind": kind,
            "value": value,
            "expires_at": expires_iso,
        },
    )
