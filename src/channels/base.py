"""Shared channel adapter contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Protocol

from src.domain.content import ContentObject


DEFAULT_CHANNEL_FLAGS: Dict[str, bool] = {
    "x": True,
    "email": False,
    "blog": False,
    "instagram": False,
}


@dataclass(frozen=True)
class ChannelPayload:
    workspace_id: str
    channel: str
    body: str
    title: str | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChannelPublishResult:
    channel: str
    published: bool
    status: str
    message: str
    external_id: str | None = None
    payload: Dict[str, Any] = field(default_factory=dict)


class ChannelAdapter(Protocol):
    channel: str

    def format(self, content: ContentObject) -> ChannelPayload:
        raise NotImplementedError

    def publish(self, payload: ChannelPayload) -> ChannelPublishResult:
        raise NotImplementedError


def resolve_channel_flags(overrides: Dict[str, bool] | None = None) -> Dict[str, bool]:
    merged = dict(DEFAULT_CHANNEL_FLAGS)
    if overrides:
        for key, value in overrides.items():
            if key in merged:
                merged[key] = bool(value)
    return merged
