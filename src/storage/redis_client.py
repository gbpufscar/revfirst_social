"""Redis client factory and health checks."""

from __future__ import annotations

from functools import lru_cache

from redis import Redis

from src.core.config import get_settings


@lru_cache(maxsize=1)
def get_client() -> Redis:
    settings = get_settings()
    return Redis.from_url(settings.redis_url, decode_responses=True)


def test_connection() -> tuple[bool, str | None]:
    try:
        get_client().ping()
        return True, None
    except Exception as exc:  # pragma: no cover
        return False, str(exc)
