from agents.intent_classifier.classifier import classify_intent


def test_classify_intent_opportunity() -> None:
    result = classify_intent("Need a tool recommendation for social workflow")
    assert result.label == "opportunity"
    assert result.confidence >= 0.7
