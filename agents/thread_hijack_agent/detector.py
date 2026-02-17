"""Detect high-value threads for tactical participation."""

from __future__ import annotations


def is_hijack_candidate(text: str, min_len: int = 40) -> bool:
    if len(text.strip()) < min_len:
        return False
    keywords = ["what tool", "how do you", "best way", "struggling with"]
    normalized = text.lower()
    return any(k in normalized for k in keywords)
