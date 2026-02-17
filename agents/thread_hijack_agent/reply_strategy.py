"""Reply strategy rules for thread hijack opportunities."""

from __future__ import annotations


def choose_reply_angle(thread_text: str) -> str:
    text = thread_text.lower()
    if "pricing" in text:
        return "share framework"
    if "hiring" in text:
        return "share checklist"
    return "share practical example"
