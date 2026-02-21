"""Control-plane command dispatcher."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict

from redis import Redis
from sqlalchemy.orm import Session

from src.control.command_schema import ControlCommand, ControlResponse, TelegramCommandEnvelope
from src.control.handlers import (
    approve,
    channel,
    growth,
    help as help_handler,
    limits,
    logs,
    metrics,
    pause,
    preview,
    queue,
    report,
    run,
    seed,
    status,
    strategy,
)
from src.control.security import ControlActor, assert_command_permission
from src.integrations.x.x_client import XClient


@dataclass(frozen=True)
class CommandContext:
    session: Session
    redis_client: Redis
    x_client: XClient
    envelope: TelegramCommandEnvelope
    command: ControlCommand
    actor: ControlActor
    request_id: str
    idempotency_key: str


Handler = Callable[[CommandContext], ControlResponse]


_HANDLER_MAP: Dict[str, Handler] = {
    "help": help_handler.handle,
    "status": status.handle,
    "metrics": metrics.handle,
    "growth": growth.handle,
    "growth_weekly": growth.handle_weekly,
    "daily_report": report.handle_daily,
    "weekly_report": report.handle_weekly,
    "queue": queue.handle,
    "preview": preview.handle,
    "approve": approve.handle,
    "reject": approve.handle_reject,
    "pause": pause.handle,
    "resume": pause.handle_resume,
    "run": run.handle,
    "channel": channel.handle,
    "limit": limits.handle,
    "logs": logs.handle,
    "seed": seed.handle,
    "strategy_scan": strategy.handle_scan,
    "strategy_report": strategy.handle_report,
}


def dispatch_command(context: CommandContext) -> ControlResponse:
    handler = _HANDLER_MAP.get(context.command.name)
    if handler is None:
        return ControlResponse(success=False, message="unknown_command", data={"command": context.command.name})
    assert_command_permission(context.actor, command_name=context.command.name)
    return handler(context)
