from __future__ import annotations

import uuid

from src.domain.agents.reply_writer import generate_reply_draft


def test_domain_reply_writer_returns_valid_contract() -> None:
    workspace_id = str(uuid.uuid4())
    draft = generate_reply_draft(
        workspace_id=workspace_id,
        source_tweet_id="190000000000000123",
        source_text="Drop your SaaS below and share your MRR.",
        intent="open_call",
        opportunity_score=82,
        max_chars=220,
    )

    assert draft.workspace_id == workspace_id
    assert draft.intent == "open_call"
    assert len(draft.text) <= 220
    assert draft.confidence >= 40
    assert "anti-hype" in draft.tags

