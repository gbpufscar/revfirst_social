"""Domain routing helpers for channel-aware content delivery."""

from src.domain.routing.channel_router import (
    CHANNEL_FLAGS_KEY_TEMPLATE,
    GLOBAL_KILL_SWITCH_KEY,
    WORKSPACE_PAUSED_KEY_TEMPLATE,
    ChannelRouteDecision,
    route_content_object,
)

__all__ = [
    "CHANNEL_FLAGS_KEY_TEMPLATE",
    "GLOBAL_KILL_SWITCH_KEY",
    "WORKSPACE_PAUSED_KEY_TEMPLATE",
    "ChannelRouteDecision",
    "route_content_object",
]
