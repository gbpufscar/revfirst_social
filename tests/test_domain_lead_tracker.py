from __future__ import annotations

import uuid

from src.domain.agents.lead_tracker import track_lead_candidate


def test_domain_lead_tracker_scores_founder_signal() -> None:
    workspace_id = str(uuid.uuid4())
    lead = track_lead_candidate(
        workspace_id=workspace_id,
        source_tweet_id="190000000000000111",
        text="Founder building a SaaS and sharing MRR milestones.",
        opportunity_score=78,
        author_handle="builder_one",
        reply_count=11,
        watch_days=7,
    )
    assert lead.workspace_id == workspace_id
    assert lead.lead_type == "founder"
    assert lead.lead_score >= 70
    assert lead.watch_days == 7
    assert any(signal.startswith("keyword:") for signal in lead.signals)

