"""Extract style markers from seed content."""

from __future__ import annotations


def extract_style_markers(text: str) -> dict[str, bool]:
    lowered = text.lower()
    return {
        "has_numbers": any(ch.isdigit() for ch in text),
        "has_question": "?" in text,
        "is_direct": "you" in lowered,
    }
