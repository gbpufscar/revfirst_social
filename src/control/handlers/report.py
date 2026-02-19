"""Reporting handlers for daily and weekly operational summaries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.control.command_schema import ControlResponse
from src.reporting.service import build_daily_report, build_weekly_report

if TYPE_CHECKING:
    from src.control.command_router import CommandContext


def handle_daily(context: "CommandContext") -> ControlResponse:
    report = build_daily_report(
        context.session,
        workspace_id=context.envelope.workspace_id,
    )
    return ControlResponse(
        success=True,
        message="daily_report_ok",
        data=report,
    )


def handle_weekly(context: "CommandContext") -> ControlResponse:
    report = build_weekly_report(
        context.session,
        workspace_id=context.envelope.workspace_id,
    )
    return ControlResponse(
        success=True,
        message="weekly_report_ok",
        data=report,
    )
