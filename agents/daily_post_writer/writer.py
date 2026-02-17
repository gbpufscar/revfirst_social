"""Daily post writer using strategy and seed memory."""

from __future__ import annotations


def write_daily_post(topic: str, insight: str) -> str:
    return f"{topic}: {insight}"[:280]
