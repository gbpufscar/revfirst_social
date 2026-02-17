"""Brand Consistency agent (pure domain logic)."""

from __future__ import annotations

import re
from typing import List

from src.domain.agents.contracts import BrandConsistencyResult


_DISALLOWED_TERMS = [
    "revolutionary",
    "game changer",
    "unbelievable",
    "guaranteed",
    "instant success",
]


_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF]")


def _normalize(text: str) -> str:
    compact = re.sub(r"\s+", " ", text.strip())
    return compact


def _find_sentence_violations(text: str, max_words_per_sentence: int = 18) -> List[str]:
    violations: List[str] = []
    sentences = [part.strip() for part in re.split(r"[.!?]+", text) if part.strip()]
    for index, sentence in enumerate(sentences, start=1):
        words = [word for word in sentence.split(" ") if word]
        if len(words) > max_words_per_sentence:
            violations.append(f"sentence_{index}_too_long")
    return violations


def validate_brand_consistency(text: str) -> BrandConsistencyResult:
    normalized = _normalize(text)
    lowered = normalized.lower()

    violations: List[str] = []
    for term in _DISALLOWED_TERMS:
        if term in lowered:
            violations.append(f"disallowed_term:{term}")

    if _EMOJI_RE.search(normalized):
        violations.append("emoji_not_allowed")

    if not any(keyword in lowered for keyword in ["founder", "builder", "revenue", "mrr"]):
        violations.append("missing_builder_first_signal")

    if "buy now" in lowered or "act now" in lowered:
        violations.append("cta_too_aggressive")

    violations.extend(_find_sentence_violations(normalized))

    score = 100
    for item in violations:
        if item.startswith("disallowed_term:"):
            score -= 25
        elif item == "emoji_not_allowed":
            score -= 20
        elif item == "cta_too_aggressive":
            score -= 15
        elif item.startswith("sentence_"):
            score -= 8
        else:
            score -= 10
    score = max(score, 0)

    blocking = any(
        v.startswith("disallowed_term:") or v in {"emoji_not_allowed", "cta_too_aggressive"}
        for v in violations
    )
    passed = (not blocking) and score >= 70
    return BrandConsistencyResult(
        passed=passed,
        score=score,
        violations=violations,
        normalized_text=normalized,
    )

