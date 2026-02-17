from agents.opportunity_scorer.scorer import score_opportunity


def test_score_opportunity_bounds() -> None:
    score = score_opportunity(0.9, 0.8, 0.7)
    assert 0.0 <= score <= 1.0
