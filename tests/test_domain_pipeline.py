from __future__ import annotations

import json
import uuid

from src.domain.agents.pipeline import evaluate_candidate_bundle


def test_domain_pipeline_returns_json_serializable_bundle() -> None:
    candidate = {
        "workspace_id": str(uuid.uuid4()),
        "source_tweet_id": "190000000000000999",
        "text": "Open thread for founders: share your SaaS and your current bottleneck.",
        "intent": "open_call",
        "opportunity_score": 76,
        "author_handle": "founder_alpha",
        "public_metrics": {"reply_count": 9, "like_count": 41, "retweet_count": 4},
    }

    bundle = evaluate_candidate_bundle(candidate)
    assert "reply_draft" in bundle
    assert "brand_consistency" in bundle
    assert "cringe_guard" in bundle
    assert "thread_detector" in bundle
    assert "lead_tracker" in bundle

    serialized = json.dumps(bundle, ensure_ascii=True, sort_keys=True)
    assert isinstance(serialized, str)
    assert "publish" not in serialized.lower()

