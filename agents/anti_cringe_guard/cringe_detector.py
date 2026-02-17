"""Basic anti-cringe heuristic checks."""

from __future__ import annotations


def detect_cringe(text: str) -> tuple[bool, list[str]]:
    issues: list[str] = []
    if "!!!" in text:
        issues.append("excessive_punctuation")
    if text.count("I") > 5:
        issues.append("self_centered")
    if "guaranteed" in text.lower():
        issues.append("overclaim")
    return (len(issues) > 0, issues)
