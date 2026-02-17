"""Reply Writer agent (pure domain logic)."""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional

from src.domain.agents.contracts import ReplyDraft


_HYPE_TERMS = {
    "revolutionary",
    "game changer",
    "unbelievable",
    "viral",
    "10x",
    "next-level",
}


def _clean_text(value: str) -> str:
    cleaned = value.strip().replace("\n", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    lowered = cleaned.lower()
    for term in _HYPE_TERMS:
        lowered = lowered.replace(term, "")
    lowered = re.sub(r"\s+", " ", lowered).strip(" .,")
    return lowered


def _build_reply(intent: str, source_text: str) -> str:
    compact = _clean_text(source_text)
    if intent == "open_call":
        return "Useful thread. We help founders turn replies into real revenue. Happy to share playbook."
    if intent == "discussion":
        return "Strong question. We see founders win when they answer with concrete numbers and a clear next step."
    if "builder" in compact or "founder" in compact:
        return "Useful point. Builder-first positioning works best when the offer is specific and measurable."
    return "Practical take: keep the message clear, useful, and tied to a measurable result."


def generate_reply_draft(
    *,
    workspace_id: str,
    source_tweet_id: Optional[str],
    source_text: str,
    intent: str,
    opportunity_score: int,
    max_chars: int = 280,
) -> ReplyDraft:
    base = _build_reply(intent, source_text)
    if len(base) > max_chars:
        base = base[: max_chars - 1].rstrip() + "."

    confidence = max(40, min(95, int(50 + (opportunity_score / 2))))
    tags = ["builder-first", "anti-hype"]
    if intent:
        tags.append(intent)
    rationale = f"Generated from intent={intent} with opportunity_score={opportunity_score}."

    return ReplyDraft(
        workspace_id=workspace_id,
        source_tweet_id=source_tweet_id,
        intent=intent or "general",
        text=base,
        confidence=confidence,
        rationale=rationale,
        tags=tags,
    )


def generate_reply_from_candidate(candidate: Mapping[str, Any], *, max_chars: int = 280) -> ReplyDraft:
    return generate_reply_draft(
        workspace_id=str(candidate.get("workspace_id") or ""),
        source_tweet_id=str(candidate.get("source_tweet_id") or "") or None,
        source_text=str(candidate.get("text") or ""),
        intent=str(candidate.get("intent") or "general"),
        opportunity_score=int(candidate.get("opportunity_score") or 0),
        max_chars=max_chars,
    )
