"""Brand consistency validator."""

from __future__ import annotations


DISALLOWED = ["revolutionary", "game changer", "unbelievable"]


def validate_brand_voice(text: str) -> tuple[bool, list[str]]:
    issues: list[str] = []
    lowered = text.lower()
    for term in DISALLOWED:
        if term in lowered:
            issues.append(f"disallowed_term:{term}")
    return (len(issues) == 0, issues)
