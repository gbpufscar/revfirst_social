from agents.brand_consistency_agent.validator import validate_brand_voice


def test_brand_voice_validator_detects_disallowed_term() -> None:
    is_valid, issues = validate_brand_voice("This is a game changer")
    assert not is_valid
    assert issues
