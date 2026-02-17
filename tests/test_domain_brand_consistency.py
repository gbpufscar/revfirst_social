from __future__ import annotations

from src.domain.agents.brand_consistency import validate_brand_consistency


def test_domain_brand_consistency_passes_builder_first_copy() -> None:
    result = validate_brand_consistency(
        "Founders win when messaging is specific and tied to revenue outcomes."
    )
    assert result.passed is True
    assert result.score >= 70


def test_domain_brand_consistency_blocks_hype_and_emoji() -> None:
    result = validate_brand_consistency(
        "This is a revolutionary game changer for founders!!! 🚀 Buy now."
    )
    assert result.passed is False
    assert any(v.startswith("disallowed_term:") for v in result.violations)
    assert "emoji_not_allowed" in result.violations

