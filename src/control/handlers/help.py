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
        "/metrics",
        "/daily_report",
        "/weekly_report",
        "/queue",
        "/preview <queue_id>",
        "/approve <queue_id>",
        "sim | aprovado (aprova o item pendente mais recente)",
        "/reject <queue_id>",
        "/pause [global]",
        "/resume [global]",
        "/run <ingest_open_calls|propose_replies|execute_approved|daily_post> [dry_run=true]",
        "/channel enable|disable <x|email|blog|instagram>",
        "/limit replies|posts <n>",
        "/seed <text>",
    ]
    return ControlResponse(
        success=True,
        message="available_commands",
        data={"commands": lines},
    )
