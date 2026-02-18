"""Pause/resume handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.control.command_schema import ControlResponse
from src.control.services import set_pause_state
from src.control.state import set_global_kill_switch, set_workspace_paused

if TYPE_CHECKING:
    from src.control.command_router import CommandContext


def _is_global(context: "CommandContext") -> bool:
    if not context.command.args:
        return False
    return context.command.args[0].strip().lower() == "global"


def handle(context: "CommandContext") -> ControlResponse:
    workspace_id = context.envelope.workspace_id
    if _is_global(context):
        if context.actor.role != "owner":
            return ControlResponse(success=False, message="global_kill_switch_requires_owner", data={})
        set_global_kill_switch(context.redis_client, enabled=True)
        return ControlResponse(success=True, message="global_kill_switch_enabled", data={"enabled": True})

    set_pause_state(context.session, workspace_id=workspace_id, paused=True)
    set_workspace_paused(context.redis_client, workspace_id=workspace_id, paused=True)
    return ControlResponse(
        success=True,
        message="workspace_paused",
        data={"workspace_id": workspace_id, "paused": True},
    )


def handle_resume(context: "CommandContext") -> ControlResponse:
    workspace_id = context.envelope.workspace_id
    if _is_global(context):
        if context.actor.role != "owner":
            return ControlResponse(success=False, message="global_kill_switch_requires_owner", data={})
        set_global_kill_switch(context.redis_client, enabled=False)
        return ControlResponse(success=True, message="global_kill_switch_disabled", data={"enabled": False})

    set_pause_state(context.session, workspace_id=workspace_id, paused=False)
    set_workspace_paused(context.redis_client, workspace_id=workspace_id, paused=False)
    return ControlResponse(
        success=True,
        message="workspace_resumed",
        data={"workspace_id": workspace_id, "paused": False},
    )
