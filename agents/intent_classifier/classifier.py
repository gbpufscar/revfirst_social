"""Classify conversation intent from candidate text."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class IntentResult:
    label: str
    confidence: float


def classify_intent(text: str) -> IntentResult:
    text_lower = text.lower()
    if any(k in text_lower for k in ["looking for", "need", "recommend", "how do"]):
        return IntentResult(label="opportunity", confidence=0.8)
    return IntentResult(label="noise", confidence=0.55)
