"""Redis-based per-workspace lock primitives for scheduler isolation."""

from __future__ import annotations

from dataclasses import dataclass
import uuid

from redis import Redis


LOCK_KEY_TEMPLATE = "revfirst:{workspace_id}:scheduler:lock"
RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
end
return 0
"""


def workspace_lock_key(workspace_id: str) -> str:
    return LOCK_KEY_TEMPLATE.format(workspace_id=workspace_id)


@dataclass(frozen=True)
class WorkspaceLockHandle:
    manager: "WorkspaceLockManager"
    workspace_id: str
    token: str
    key: str

    def release(self) -> bool:
        return self.manager.release(self.workspace_id, self.token)


class WorkspaceLockManager:
    """Acquire and release one lock per workspace using Redis SET NX EX."""

    def __init__(self, redis_client: Redis, *, ttl_seconds: int = 300) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._redis = redis_client
        self._ttl_seconds = ttl_seconds

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    def lock_key(self, workspace_id: str) -> str:
        return workspace_lock_key(workspace_id)

    def acquire(self, workspace_id: str) -> WorkspaceLockHandle | None:
        key = self.lock_key(workspace_id)
        token = str(uuid.uuid4())
        acquired = self._redis.set(key, token, nx=True, ex=self._ttl_seconds)
        if not acquired:
            return None
        return WorkspaceLockHandle(
            manager=self,
            workspace_id=workspace_id,
            token=token,
            key=key,
        )

    def release(self, workspace_id: str, token: str) -> bool:
        key = self.lock_key(workspace_id)
        released = self._redis.eval(RELEASE_LOCK_SCRIPT, 1, key, token)
        return int(released) == 1

