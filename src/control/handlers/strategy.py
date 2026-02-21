"""Strategy scan/report handlers for benchmark-based growth patterns."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.control.command_schema import ControlResponse
from src.strategy.x_growth_strategy_agent import (
    latest_workspace_strategy_report,
    run_workspace_strategy_scan,
    upsert_watchlist_account,
)

if TYPE_CHECKING:
    from src.control.command_router import CommandContext


def _parse_add_watchlist_args(args: list[str]) -> tuple[str, str | None] | None:
    if len(args) < 1:
        return None
    account_user_id = str(args[0]).strip()
    if not account_user_id:
        return None
    account_username = str(args[1]).strip() if len(args) > 1 else None
    if account_username == "":
        account_username = None
    return account_user_id, account_username


def handle_scan(context: "CommandContext") -> ControlResponse:
    workspace_id = context.envelope.workspace_id
    args = list(context.command.args)
    if args and args[0].lower() not in {"run", "scan"}:
        parsed = _parse_add_watchlist_args(args)
        if parsed is None:
            return ControlResponse(success=False, message="strategy_watchlist_args_invalid", data={})
        account_user_id, account_username = parsed
        row = upsert_watchlist_account(
            context.session,
            workspace_id=workspace_id,
            account_user_id=account_user_id,
            account_username=account_username,
            added_by_user_id=context.actor.user_id,
        )
        return ControlResponse(
            success=True,
            message="strategy_watchlist_updated",
            data={
                "workspace_id": workspace_id,
                "account_user_id": row.account_user_id,
                "account_username": row.account_username,
                "status": row.status,
            },
        )

    result = run_workspace_strategy_scan(
        context.session,
        workspace_id=workspace_id,
        x_client=context.x_client,
    )
    if result.get("status") in {"missing_x_oauth", "no_watchlist"}:
        return ControlResponse(success=False, message="strategy_scan_not_ready", data=result)
    return ControlResponse(success=True, message="strategy_scan_ok", data=result)


def handle_report(context: "CommandContext") -> ControlResponse:
    workspace_id = context.envelope.workspace_id
    report = latest_workspace_strategy_report(
        context.session,
        workspace_id=workspace_id,
    )
    if not bool(report.get("available")):
        return ControlResponse(success=False, message="strategy_report_empty", data=report)
    return ControlResponse(success=True, message="strategy_report_ok", data=report)

