"""Anti-Cringe guard agent (pure domain logic)."""

from __future__ import annotations

import re
from typing import List

from src.domain.agents.contracts import CringeCheckResult


_OVERCLAIM_PATTERNS = [
    r"\bguaranteed\b",
    r"\b10x\b",
    r"\bovernight\b",
    r"\bno effort\b",
    r"\bsecret formula\b",
]


def evaluate_cringe(text: str) -> CringeCheckResult:
    normalized = text.strip()
    lowered = normalized.lower()
    flags: List[str] = []
    risk_score = 0

    if "!!!" in normalized or "???" in normalized:
        flags.append("excessive_punctuation")
        risk_score += 25

    if re.search(r"\b[A-Z]{4,}\b", normalized):
        flags.append("aggressive_all_caps")
        risk_score += 20

    if len(re.findall(r"\b(i|me|my)\b", lowered)) >= 5:
        flags.append("self_centered")
        risk_score += 15

    for pattern in _OVERCLAIM_PATTERNS:
        if re.search(pattern, lowered):
            flags.append("overclaim")
            risk_score += 25
            break

    if "buy now" in lowered or "dm me now" in lowered:
        flags.append("pushy_cta")
        risk_score += 20

    if "bro" in lowered or "guru" in lowered:
        flags.append("buzzword_tone")
        risk_score += 10

    risk_score = max(0, min(risk_score, 100))
    cringe = risk_score >= 30
    return CringeCheckResult(cringe=cringe, risk_score=risk_score, flags=flags)

