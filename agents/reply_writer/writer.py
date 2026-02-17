"""Reply generation module."""

from __future__ import annotations


def write_reply(context: str, max_chars: int = 280) -> str:
    base = f"Useful take: {context.strip()}"
    return base[:max_chars]
