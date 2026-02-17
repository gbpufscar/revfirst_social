"""Strategy optimization from metrics snapshots."""

from __future__ import annotations


def recommend_focus(avg_engagement: float, conversion_rate: float) -> str:
    if conversion_rate < 0.02:
        return "improve qualification in replies"
    if avg_engagement < 0.03:
        return "increase topical relevance"
    return "scale current strategy with safeguards"
