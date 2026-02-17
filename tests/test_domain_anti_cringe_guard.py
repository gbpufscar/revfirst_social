from __future__ import annotations

from src.domain.agents.anti_cringe_guard import evaluate_cringe


def test_domain_anti_cringe_guard_detects_risky_copy() -> None:
    result = evaluate_cringe("BUY NOW!!! Guaranteed 10x results overnight.")
    assert result.cringe is True
    assert result.risk_score >= 30
    assert "overclaim" in result.flags


def test_domain_anti_cringe_guard_allows_practical_copy() -> None:
    result = evaluate_cringe(
        "Practical approach: founders should test a clear offer and track conversion weekly."
    )
    assert result.cringe is False
    assert result.risk_score < 30

