"""Redis-backed control-plane transient state helpers."""

from __future__ import annotations

from dataclasses import dataclass
import uuid

from redis import Redis


_GLOBAL_KILL_SWITCH_KEY = "revfirst:control:global_kill_switch"
_WORKSPACE_PAUSE_KEY = "revfirst:{workspace_id}:control:paused"
_CHANNEL_CACHE_KEY = "revfirst:{workspace_id}:control:channels"
_PIPELINE_RUN_LOCK_KEY = "revfirst:{workspace_id}:control:run:{pipeline}:lock"

_RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
end
return 0
"""


@dataclass(frozen=True)
class PipelineRunLock:
    redis_client: Redis
    key: str
    token: str

    def release(self) -> bool:
        released = self.redis_client.eval(_RELEASE_LOCK_SCRIPT, 1, self.key, self.token)
        return int(released) == 1


def global_kill_switch_key() -> str:
    return _GLOBAL_KILL_SWITCH_KEY


def workspace_pause_key(workspace_id: str) -> str:
    return _WORKSPACE_PAUSE_KEY.format(workspace_id=workspace_id)


def channel_cache_key(workspace_id: str) -> str:
    return _CHANNEL_CACHE_KEY.format(workspace_id=workspace_id)


def pipeline_run_lock_key(workspace_id: str, pipeline: str) -> str:
    return _PIPELINE_RUN_LOCK_KEY.format(workspace_id=workspace_id, pipeline=pipeline)


def is_global_kill_switch(redis_client: Redis) -> bool:
    value = redis_client.get(global_kill_switch_key())
    return str(value).strip().lower() == "true"


def set_global_kill_switch(redis_client: Redis, *, enabled: bool) -> None:
    if enabled:
        redis_client.set(global_kill_switch_key(), "true")
        return
    redis_client.delete(global_kill_switch_key())


def is_workspace_paused(redis_client: Redis, *, workspace_id: str) -> bool:
    value = redis_client.get(workspace_pause_key(workspace_id))
    return str(value).strip().lower() == "true"


def set_workspace_paused(redis_client: Redis, *, workspace_id: str, paused: bool) -> None:
    key = workspace_pause_key(workspace_id)
    if paused:
        redis_client.set(key, "true")
        return
    redis_client.delete(key)


def cache_channels(redis_client: Redis, *, workspace_id: str, channels_json: str) -> None:
    redis_client.set(channel_cache_key(workspace_id), channels_json)


def acquire_pipeline_run_lock(
    redis_client: Redis,
    *,
    workspace_id: str,
    pipeline: str,
    ttl_seconds: int,
) -> PipelineRunLock | None:
    token = str(uuid.uuid4())
    key = pipeline_run_lock_key(workspace_id, pipeline)
    acquired = redis_client.set(key, token, nx=True, ex=max(1, ttl_seconds))
    if not acquired:
        return None
    return PipelineRunLock(redis_client=redis_client, key=key, token=token)
