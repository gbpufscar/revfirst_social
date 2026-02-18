"""Channel feature-flag handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.control.command_schema import ControlResponse
from src.control.services import parse_channels, set_channel_state
from src.control.state import cache_channels

if TYPE_CHECKING:
    from src.control.command_router import CommandContext


def handle(context: "CommandContext") -> ControlResponse:
    if len(context.command.args) < 2:
        return ControlResponse(success=False, message="usage: /channel enable|disable <channel>", data={})

    operation = context.command.args[0].strip().lower()
    channel = context.command.args[1].strip().lower()

    if operation not in {"enable", "disable"}:
        return ControlResponse(success=False, message="invalid_channel_operation", data={"operation": operation})

    try:
        setting = set_channel_state(
            context.session,
            workspace_id=context.envelope.workspace_id,
            channel=channel,
            enabled=(operation == "enable"),
        )
    except ValueError as exc:
        return ControlResponse(success=False, message=str(exc), data={"channel": channel})

    channels = parse_channels(setting)
    cache_channels(
        context.redis_client,
        workspace_id=context.envelope.workspace_id,
        channels_json=setting.channels_json,
    )

    return ControlResponse(
        success=True,
        message="channel_updated",
        data={
            "workspace_id": context.envelope.workspace_id,
            "channel": channel,
            "enabled": channels.get(channel, False),
            "channels": channels,
        },
    )
