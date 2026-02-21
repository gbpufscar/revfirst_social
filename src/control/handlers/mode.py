"""Operational mode command handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.control.command_schema import ControlResponse
from src.control.services import (
    VALID_OPERATIONAL_MODES,
    get_workspace_operational_mode,
    set_operational_mode,
)

if TYPE_CHECKING:
    from src.control.command_router import CommandContext


_MODE_TRANSITION_USAGE = "usage: /mode OR /mode set <manual|semi_autonomous|autonomous_limited|containment> [confirm]"


def _current_mode_payload(context: "CommandContext") -> dict[str, object]:
    mode = get_workspace_operational_mode(
        context.session,
        workspace_id=context.envelope.workspace_id,
        redis_client=context.redis_client,
    )
    return {
        "workspace_id": context.envelope.workspace_id,
        "mode": mode,
        "valid_modes": sorted(VALID_OPERATIONAL_MODES),
    }


def handle(context: "CommandContext") -> ControlResponse:
    args = [str(value).strip().lower() for value in context.command.args if str(value).strip()]
    if not args:
        return ControlResponse(success=True, message="mode_ok", data=_current_mode_payload(context))

    if args[0] != "set" or len(args) < 2:
        return ControlResponse(success=False, message="mode_invalid_args", data={"usage": _MODE_TRANSITION_USAGE})

    target_mode = args[1]
    if target_mode not in VALID_OPERATIONAL_MODES:
        return ControlResponse(
            success=False,
            message="mode_invalid_target",
            data={"mode": target_mode, "valid_modes": sorted(VALID_OPERATIONAL_MODES)},
        )

    if target_mode == "autonomous_limited" and "confirm" not in set(args[2:]):
        return ControlResponse(
            success=False,
            message="mode_set_requires_confirmation",
            data={
                "mode": target_mode,
                "confirmation_token": "confirm",
                "hint": "/mode set autonomous_limited confirm",
            },
        )

    setting = set_operational_mode(
        context.session,
        workspace_id=context.envelope.workspace_id,
        mode=target_mode,
        changed_by_user_id=context.actor.user_id,
        redis_client=context.redis_client,
    )
    return ControlResponse(
        success=True,
        message="mode_updated",
        data={
            "workspace_id": context.envelope.workspace_id,
            "mode": setting.operational_mode,
            "last_mode_change_at": setting.last_mode_change_at.isoformat() if setting.last_mode_change_at else None,
            "changed_by_user_id": setting.mode_changed_by_user_id,
        },
    )
