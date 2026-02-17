"""Lead watchlist scanner."""

from __future__ import annotations


def should_alert(last_seen_minutes: int, max_silence_minutes: int = 180) -> bool:
    return last_seen_minutes >= max_silence_minutes
