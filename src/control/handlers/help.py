"""Help command handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.control.command_schema import ControlResponse

if TYPE_CHECKING:
    from src.control.command_router import CommandContext


def handle(context: "CommandContext") -> ControlResponse:
    del context
    lines = [
        "/help",
        "/status",
        "/mode",
        "/mode set <manual|semi_autonomous|autonomous_limited|containment> [confirm]",
        "/stability",
        "/stability contain",
        "/ack_kill_switch",
        "/metrics",
        "/growth",
        "/growth_weekly",
        "/daily_report",
        "/weekly_report",
        "/queue",
        "/preview <queue_id>",
        "/approve <queue_id> [override]",
        "sim | aprovado (aprova o item pendente mais recente)",
        "/reject <queue_id>",
        "/pause [global]",
        "/resume [global]",
        "/run <ingest_open_calls|propose_replies|execute_approved|daily_post> [dry_run=true]",
        "/channel enable|disable <x|email|blog|instagram>",
        "/limit replies|posts <n>",
        "/seed <text>",
        "/strategy_scan <account_user_id> [account_username]",
        "/strategy_scan run",
        "/strategy_discover run",
        "/strategy_discover criteria",
        "/strategy_discover queue",
        "/strategy_discover approve <candidate_id>",
        "/strategy_discover reject <candidate_id>",
        "/strategy_report",
    ]
    return ControlResponse(
        success=True,
        message="available_commands",
        data={"commands": lines},
    )
