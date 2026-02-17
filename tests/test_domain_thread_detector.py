from __future__ import annotations

from src.domain.agents.thread_detector import detect_thread_opportunity


def test_domain_thread_detector_marks_open_call_as_hijackable() -> None:
    result = detect_thread_opportunity(
        {
            "text": "Open thread: drop your SaaS and what you are building.",
            "intent": "open_call",
            "public_metrics": {"reply_count": 18, "like_count": 70, "retweet_count": 12},
        }
    )
    assert result.should_hijack is True
    assert result.context_type == "open_call"
    assert result.score >= 60


def test_domain_thread_detector_rejects_low_fit_topic() -> None:
    result = detect_thread_opportunity(
        {
            "text": "Massive giveaway and hiring promo thread",
            "intent": "general",
            "public_metrics": {"reply_count": 1, "like_count": 2, "retweet_count": 0},
        }
    )
    assert result.should_hijack is False
    assert result.score < 60

