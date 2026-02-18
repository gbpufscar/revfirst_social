"""IP rate limiting primitives."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from threading import Lock
import time
from typing import Dict, Protocol, Tuple

from src.core.config import get_settings
from src.storage.redis_client import get_client


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int


class IPRateLimiter(Protocol):
    def check(self, *, ip: str) -> RateLimitDecision:
        """Return a decision for this IP."""


class InMemoryIPRateLimiter:
    def __init__(self, *, requests_per_window: int, window_seconds: int) -> None:
        if requests_per_window <= 0:
            raise ValueError("requests_per_window must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")

        self._limit = requests_per_window
        self._window = window_seconds
        self._lock = Lock()
        self._store: Dict[Tuple[str, int], int] = {}

    def check(self, *, ip: str) -> RateLimitDecision:
        now = int(time.time())
        window_id = now // self._window
        reset_seconds = self._window - (now % self._window)
        key = (ip, window_id)

        with self._lock:
            # Keep current and previous windows only.
            stale_keys = [item for item in self._store if item[1] < window_id - 1]
            for stale in stale_keys:
                self._store.pop(stale, None)

            count = int(self._store.get(key, 0)) + 1
            self._store[key] = count

        allowed = count <= self._limit
        remaining = max(self._limit - count, 0)
        return RateLimitDecision(
            allowed=allowed,
            limit=self._limit,
            remaining=remaining,
            reset_seconds=reset_seconds,
        )


class RedisIPRateLimiter:
    def __init__(self, *, requests_per_window: int, window_seconds: int) -> None:
        if requests_per_window <= 0:
            raise ValueError("requests_per_window must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")

        self._limit = requests_per_window
        self._window = window_seconds
        self._redis = get_client()

    def check(self, *, ip: str) -> RateLimitDecision:
        now = int(time.time())
        window_id = now // self._window
        reset_seconds = self._window - (now % self._window)
        key = f"revfirst:ratelimit:ip:{ip}:{window_id}"

        try:
            count = int(self._redis.incr(key))
            if count == 1:
                self._redis.expire(key, self._window + 1)
        except Exception:
            return RateLimitDecision(
                allowed=True,
                limit=self._limit,
                remaining=self._limit,
                reset_seconds=reset_seconds,
            )

        allowed = count <= self._limit
        remaining = max(self._limit - count, 0)
        return RateLimitDecision(
            allowed=allowed,
            limit=self._limit,
            remaining=remaining,
            reset_seconds=reset_seconds,
        )


@lru_cache(maxsize=1)
def get_ip_rate_limiter() -> IPRateLimiter:
    settings = get_settings()
    env = settings.env.lower()

    if env in {"prod", "production"}:
        return RedisIPRateLimiter(
            requests_per_window=settings.ip_rate_limit_requests_per_window,
            window_seconds=settings.ip_rate_limit_window_seconds,
        )
    return InMemoryIPRateLimiter(
        requests_per_window=settings.ip_rate_limit_requests_per_window,
        window_seconds=settings.ip_rate_limit_window_seconds,
    )

