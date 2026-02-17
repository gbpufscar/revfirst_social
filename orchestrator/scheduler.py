"""Simple scheduler utilities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def next_tick(minutes: int = 30) -> datetime:
    now = datetime.now(timezone.utc)
    minute_bucket = (now.minute // minutes + 1) * minutes
    delta_minutes = minute_bucket - now.minute
    return now.replace(second=0, microsecond=0) + timedelta(minutes=delta_minutes)
