"""Simple in-memory token bucket rate limiter."""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class RateLimiter:
    capacity: int
    refill_per_second: float
    tokens: float | None = None
    last_refill: float | None = None

    def __post_init__(self) -> None:
        now = time.monotonic()
        self.tokens = float(self.capacity)
        self.last_refill = now

    def allow(self, cost: float = 1.0) -> bool:
        now = time.monotonic()
        elapsed = now - (self.last_refill or now)
        self.tokens = min(float(self.capacity), (self.tokens or 0.0) + elapsed * self.refill_per_second)
        self.last_refill = now
        if (self.tokens or 0.0) >= cost:
            self.tokens = (self.tokens or 0.0) - cost
            return True
        return False
