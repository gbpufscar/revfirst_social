from agents.anti_cringe_guard.cringe_detector import detect_cringe


def test_detect_cringe_overclaim() -> None:
    flagged, issues = detect_cringe("Guaranteed results!!!")
    assert flagged
    assert "overclaim" in issues
