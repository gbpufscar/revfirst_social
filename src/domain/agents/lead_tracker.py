"""Lead Tracker agent (pure domain logic)."""

from __future__ import annotations

from typing import Any, List, Mapping, Optional

from src.domain.agents.contracts import LeadSignal


def _detect_lead_type(text: str) -> str:
    lowered = text.lower()
    if "founder" in lowered or "building" in lowered or "startup" in lowered:
        return "founder"
    if "growth" in lowered or "acquisition" in lowered:
        return "growth_builder"
    if "saas" in lowered or "mrr" in lowered:
        return "saas_operator"
    return "unknown"


def _extract_signals(text: str, opportunity_score: int, reply_count: int) -> List[str]:
    signals: List[str] = []
    lowered = text.lower()

    for keyword in ["founder", "builder", "startup", "saas", "mrr", "revenue", "customers"]:
        if keyword in lowered:
            signals.append(f"keyword:{keyword}")

    if opportunity_score >= 70:
        signals.append("high_opportunity_score")
    elif opportunity_score >= 50:
        signals.append("medium_opportunity_score")

    if reply_count >= 10:
        signals.append("high_conversation_engagement")
    elif reply_count >= 5:
        signals.append("medium_conversation_engagement")

    return signals


def track_lead_candidate(
    *,
    workspace_id: str,
    source_tweet_id: str,
    text: str,
    opportunity_score: int,
    author_handle: Optional[str] = None,
    reply_count: int = 0,
    watch_days: int = 7,
) -> LeadSignal:
    lead_type = _detect_lead_type(text)
    signals = _extract_signals(text, opportunity_score, reply_count)

    score = min(100, max(0, int(opportunity_score * 0.7 + min(reply_count, 20) * 1.5)))
    if lead_type == "founder":
        score = min(100, score + 10)
    if lead_type == "unknown":
        score = max(0, score - 10)

    return LeadSignal(
        workspace_id=workspace_id,
        source_tweet_id=source_tweet_id,
        author_handle=author_handle,
        lead_type=lead_type,
        lead_score=score,
        signals=signals,
        watch_days=watch_days,
    )


def track_lead_from_candidate(candidate: Mapping[str, Any], *, watch_days: int = 7) -> LeadSignal:
    metrics = candidate.get("public_metrics") or {}
    return track_lead_candidate(
        workspace_id=str(candidate.get("workspace_id") or ""),
        source_tweet_id=str(candidate.get("source_tweet_id") or ""),
        text=str(candidate.get("text") or ""),
        opportunity_score=int(candidate.get("opportunity_score") or 0),
        author_handle=str(candidate.get("author_handle") or "") or None,
        reply_count=int(metrics.get("reply_count") or 0),
        watch_days=watch_days,
    )

