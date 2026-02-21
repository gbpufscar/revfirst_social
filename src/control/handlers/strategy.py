"""Strategy scan/report handlers for benchmark-based growth patterns."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.control.command_schema import ControlResponse
from src.strategy.x_growth_strategy_agent import (
    approve_strategy_candidate,
    build_x_profile_url,
    get_strategy_discovery_criteria,
    list_pending_strategy_candidates,
    parse_discovery_candidate_rationale,
    latest_workspace_strategy_report,
    reject_strategy_candidate,
    run_workspace_strategy_discovery,
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


def handle_discover(context: "CommandContext") -> ControlResponse:
    workspace_id = context.envelope.workspace_id
    args = list(context.command.args)
    if not args or args[0].lower() in {"run", "scan"}:
        result = run_workspace_strategy_discovery(
            context.session,
            workspace_id=workspace_id,
            x_client=context.x_client,
        )
        if result.get("status") in {"missing_x_oauth", "search_failed"}:
            return ControlResponse(success=False, message="strategy_discovery_not_ready", data=result)
        return ControlResponse(success=True, message="strategy_discovery_ok", data=result)

    subcommand = args[0].lower()
    if subcommand in {"criteria", "criterios", "rules"}:
        return ControlResponse(
            success=True,
            message="strategy_discovery_criteria",
            data={
                "workspace_id": workspace_id,
                "criteria": get_strategy_discovery_criteria(),
            },
        )

    if subcommand in {"queue", "list"}:
        rows = list_pending_strategy_candidates(
            context.session,
            workspace_id=workspace_id,
            limit=10,
        )
        criteria = get_strategy_discovery_criteria()
        items = []
        for row in rows:
            rationale = parse_discovery_candidate_rationale(row)
            items.append(
                {
                    "candidate_id": row.id,
                    "account_user_id": row.account_user_id,
                    "account_username": row.account_username,
                    "profile_url": build_x_profile_url(
                        account_user_id=row.account_user_id,
                        account_username=row.account_username,
                    ),
                    "score": row.score,
                    "followers_count": row.followers_count,
                    "signal_post_count": row.signal_post_count,
                    "avg_engagement": getattr(row, "avg_engagement", None),
                    "cadence_per_day": getattr(row, "cadence_per_day", None),
                    "engagement_rate_pct": rationale.get("engagement_rate_pct"),
                    "selection_reason": rationale.get("selection_reason"),
                    "quality_checks": rationale.get("quality_checks"),
                    "status": row.status,
                    "discovered_at": row.discovered_at.isoformat(),
                }
            )
        message = "strategy_candidates_queue" if items else "strategy_candidates_queue_empty"
        return ControlResponse(
            success=True,
            message=message,
            data={
                "workspace_id": workspace_id,
                "count": len(items),
                "criteria": criteria,
                "items": items,
            },
        )

    if subcommand in {"approve", "reject"}:
        if len(args) < 2 or not str(args[1]).strip():
            return ControlResponse(success=False, message="strategy_candidate_missing_id", data={})
        candidate_id = str(args[1]).strip()
        if subcommand == "approve":
            approved = approve_strategy_candidate(
                context.session,
                workspace_id=workspace_id,
                candidate_id=candidate_id,
                reviewed_by_user_id=context.actor.user_id,
            )
            if approved is None:
                return ControlResponse(
                    success=False,
                    message="strategy_candidate_not_found",
                    data={"candidate_id": candidate_id},
                )
            return ControlResponse(success=True, message="strategy_candidate_approved", data=approved)

        rejected = reject_strategy_candidate(
            context.session,
            workspace_id=workspace_id,
            candidate_id=candidate_id,
            reviewed_by_user_id=context.actor.user_id,
        )
        if rejected is None:
            return ControlResponse(
                success=False,
                message="strategy_candidate_not_found",
                data={"candidate_id": candidate_id},
            )
        return ControlResponse(success=True, message="strategy_candidate_rejected", data=rejected)

    return ControlResponse(success=False, message="strategy_discovery_invalid_args", data={"args": args})
