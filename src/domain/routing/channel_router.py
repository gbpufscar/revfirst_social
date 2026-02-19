"""Workspace-safe channel routing for ContentObject payloads."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Protocol

from sqlalchemy.orm import Session

from src.billing.plans import check_plan_limit
from src.channels.base import DEFAULT_CHANNEL_FLAGS, resolve_channel_flags
from src.domain.content import ContentObject
from src.storage.redis_client import get_client as get_redis_client


WORKSPACE_PAUSED_KEY_TEMPLATE = "revfirst:{workspace_id}:control:paused"
GLOBAL_KILL_SWITCH_KEY = "revfirst:control:global_kill_switch"
CHANNEL_FLAGS_KEY_TEMPLATE = "revfirst:{workspace_id}:control:channel_flags"


class RedisLike(Protocol):
    def get(self, key: str) -> Optional[str]:
        raise NotImplementedError

    def hgetall(self, key: str) -> Mapping[str, str]:
        raise NotImplementedError


@dataclass(frozen=True)
class ChannelRouteDecision:
    workspace_id: str
    requested_targets: List[str] = field(default_factory=list)
    resolved_targets: List[str] = field(default_factory=list)
    blocked_targets: Dict[str, str] = field(default_factory=dict)
    paused: bool = False
    global_kill_switch: bool = False
    plan_limited: bool = False


def _truthy(value: object) -> bool:
    if value is None:
        return False
    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "yes", "on", "enabled"}


def _workspace_paused_key(workspace_id: str) -> str:
    return WORKSPACE_PAUSED_KEY_TEMPLATE.format(workspace_id=workspace_id)


def _workspace_channel_flags_key(workspace_id: str) -> str:
    return CHANNEL_FLAGS_KEY_TEMPLATE.format(workspace_id=workspace_id)


def _safe_redis(redis_client: RedisLike | None) -> RedisLike | None:
    if redis_client is not None:
        return redis_client
    try:
        return get_redis_client()
    except Exception:
        return None


def _load_workspace_channel_flags(redis_client: RedisLike | None, workspace_id: str) -> Dict[str, bool]:
    if redis_client is None:
        return {}

    try:
        raw = redis_client.hgetall(_workspace_channel_flags_key(workspace_id))
    except Exception:
        return {}

    parsed: Dict[str, bool] = {}
    for key, value in dict(raw).items():
        channel = str(key).strip().lower()
        if channel not in DEFAULT_CHANNEL_FLAGS:
            continue
        parsed[channel] = _truthy(value)
    return parsed


def _action_for_content(content: ContentObject) -> str:
    if content.content_type == "reply":
        return "publish_reply"
    return "publish_post"


def route_content_object(
    session: Session,
    *,
    content: ContentObject,
    redis_client: RedisLike | None = None,
    channel_overrides: Optional[Dict[str, bool]] = None,
    enforce_plan_limits: bool = False,
) -> ChannelRouteDecision:
    """Resolve channel targets considering pause, flags, and plan limits."""

    requested_targets = list(content.channel_targets)
    redis = _safe_redis(redis_client)
    blocked_targets: Dict[str, str] = {}

    if redis is not None:
        try:
            if _truthy(redis.get(GLOBAL_KILL_SWITCH_KEY)):
                for target in requested_targets:
                    blocked_targets[target] = "global_kill_switch"
                return ChannelRouteDecision(
                    workspace_id=content.workspace_id,
                    requested_targets=requested_targets,
                    blocked_targets=blocked_targets,
                    global_kill_switch=True,
                )
        except Exception:
            pass

        try:
            if _truthy(redis.get(_workspace_paused_key(content.workspace_id))):
                for target in requested_targets:
                    blocked_targets[target] = "workspace_paused"
                return ChannelRouteDecision(
                    workspace_id=content.workspace_id,
                    requested_targets=requested_targets,
                    blocked_targets=blocked_targets,
                    paused=True,
                )
        except Exception:
            pass

    workspace_flags = _load_workspace_channel_flags(redis, content.workspace_id)
    resolved_flags = resolve_channel_flags(workspace_flags)
    if channel_overrides:
        for key, value in channel_overrides.items():
            channel = str(key).strip().lower()
            if channel in resolved_flags:
                resolved_flags[channel] = bool(value)

    resolved_targets: List[str] = []
    for target in requested_targets:
        if not resolved_flags.get(target, False):
            blocked_targets[target] = "channel_disabled"
            continue
        resolved_targets.append(target)

    plan_limited = False
    if enforce_plan_limits and "x" in resolved_targets:
        action = _action_for_content(content)
        decision = check_plan_limit(
            session,
            workspace_id=content.workspace_id,
            action=action,
            requested=1,
        )
        if not decision.allowed:
            resolved_targets = [target for target in resolved_targets if target != "x"]
            blocked_targets["x"] = "plan_limit_exceeded"
            plan_limited = True

    return ChannelRouteDecision(
        workspace_id=content.workspace_id,
        requested_targets=requested_targets,
        resolved_targets=resolved_targets,
        blocked_targets=blocked_targets,
        plan_limited=plan_limited,
    )
