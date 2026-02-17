"""Thread Detector agent (pure domain logic)."""

from __future__ import annotations

from typing import Any, List, Mapping

from src.domain.agents.contracts import ThreadDetectionResult


_OPEN_CALL_KEYWORDS = [
    "drop your saas",
    "show your product",
    "share your startup",
    "what are you building",
    "open thread",
]


def detect_thread_opportunity(candidate: Mapping[str, Any]) -> ThreadDetectionResult:
    text = str(candidate.get("text") or "").lower()
    intent = str(candidate.get("intent") or "general").lower()
    metrics = candidate.get("public_metrics") or {}
    reply_count = int(metrics.get("reply_count") or 0)
    like_count = int(metrics.get("like_count") or 0)
    retweet_count = int(metrics.get("retweet_count") or 0)

    score = 20
    reasons: List[str] = []
    context_type = "general_thread"

    if intent == "open_call" or any(keyword in text for keyword in _OPEN_CALL_KEYWORDS):
        score += 35
        reasons.append("open_call_signal")
        context_type = "open_call"

    if "builder" in text or "founder" in text or "saas" in text:
        score += 15
        reasons.append("builder_context")
        if context_type == "general_thread":
            context_type = "builder_thread"

    if reply_count >= 10:
        score += 15
        reasons.append("active_reply_volume")
    elif reply_count >= 5:
        score += 8
        reasons.append("moderate_reply_volume")

    if like_count >= 50:
        score += 10
        reasons.append("high_like_volume")

    if retweet_count >= 10:
        score += 5
        reasons.append("high_retweet_volume")

    if "hiring" in text or "giveaway" in text:
        score -= 15
        reasons.append("low_fit_topic")

    score = max(0, min(score, 100))
    should_hijack = score >= 60 and context_type in {"open_call", "builder_thread"}
    return ThreadDetectionResult(
        should_hijack=should_hijack,
        score=score,
        context_type=context_type,
        reasons=reasons,
    )

