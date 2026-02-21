"""Global kill-switch ack handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.control.command_schema import ControlResponse
from src.operations.stability_guard_agent import ack_global_kill_switch

if TYPE_CHECKING:
    from src.control.command_router import CommandContext


def handle_ack(context: "CommandContext") -> ControlResponse:
    if context.actor.role != "owner":
        return ControlResponse(success=False, message="kill_switch_requires_owner", data={})

    payload = ack_global_kill_switch(
        context.session,
        workspace_id=context.envelope.workspace_id,
        redis_client=context.redis_client,
        actor_user_id=context.actor.user_id,
    )
    if payload.get("acknowledged"):
        return ControlResponse(success=True, message="kill_switch_acknowledged", data=payload)
    return ControlResponse(success=False, message="kill_switch_not_enabled", data=payload)
