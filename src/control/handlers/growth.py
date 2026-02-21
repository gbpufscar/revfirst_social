"""Growth analysis handlers for Telegram command center."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.analytics.x_performance_agent import build_workspace_growth_report, collect_workspace_growth_snapshot
from src.control.command_schema import ControlResponse

if TYPE_CHECKING:
    from src.control.command_router import CommandContext


def handle(context: "CommandContext") -> ControlResponse:
    workspace_id = context.envelope.workspace_id
    snapshot = collect_workspace_growth_snapshot(
        context.session,
        workspace_id=workspace_id,
        x_client=context.x_client,
    )
    report = build_workspace_growth_report(
        context.session,
        workspace_id=workspace_id,
        period_days=1,
    )
    report["snapshot"] = snapshot
    return ControlResponse(success=True, message="growth_report_ok", data=report)


def handle_weekly(context: "CommandContext") -> ControlResponse:
    workspace_id = context.envelope.workspace_id
    report = build_workspace_growth_report(
        context.session,
        workspace_id=workspace_id,
        period_days=7,
    )
    return ControlResponse(success=True, message="growth_weekly_ok", data=report)

