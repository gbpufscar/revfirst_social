"""Opportunity scoring for ranked response priority."""

from __future__ import annotations


def score_opportunity(intent_confidence: float, audience_fit: float, urgency: float) -> float:
    weighted = (intent_confidence * 0.5) + (audience_fit * 0.3) + (urgency * 0.2)
    return max(0.0, min(1.0, weighted))
