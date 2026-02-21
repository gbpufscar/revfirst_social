"""Stability guard handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.control.command_schema import ControlResponse
from src.operations.stability_guard_agent import (
    apply_stability_containment,
    build_workspace_stability_report,
    run_workspace_stability_guard_cycle,
)

if TYPE_CHECKING:
    from src.control.command_router import CommandContext


def handle(context: "CommandContext") -> ControlResponse:
    workspace_id = context.envelope.workspace_id
    args = [str(value).strip().lower() for value in context.command.args if str(value).strip()]

    if not args:
        report = run_workspace_stability_guard_cycle(
            context.session,
            workspace_id=workspace_id,
            redis_client=context.redis_client,
            actor_user_id=context.actor.user_id,
            trigger="telegram",
        )
        containment = report.get("containment") if isinstance(report.get("containment"), dict) else {}
        kill_switch_action = report.get("kill_switch_action") if isinstance(report.get("kill_switch_action"), dict) else {}
        if kill_switch_action.get("applied"):
            return ControlResponse(success=True, message="stability_kill_switch_applied", data=report)
        if containment.get("actions_applied"):
            return ControlResponse(success=True, message="stability_auto_containment_applied", data=report)
        return ControlResponse(success=True, message="stability_report_ok", data=report)

    subcommand = args[0]
    if subcommand not in {"contain", "apply", "pause"}:
        return ControlResponse(success=False, message="stability_invalid_args", data={"args": args})

    if context.actor.role not in {"owner", "admin"}:
        return ControlResponse(success=False, message="stability_containment_requires_admin", data={})

    report = build_workspace_stability_report(
        context.session,
        workspace_id=workspace_id,
        redis_client=context.redis_client,
    )
    containment = apply_stability_containment(
        context.session,
        workspace_id=workspace_id,
        redis_client=context.redis_client,
        report=report,
        actor_user_id=context.actor.user_id,
        trigger="telegram_manual",
    )
    data = dict(report)
    data.update(containment)
    if containment.get("actions_applied"):
        return ControlResponse(success=True, message="stability_containment_applied", data=data)
    return ControlResponse(success=True, message="stability_containment_not_required", data=data)
