"""Workspace-scoped orchestration pipeline used by the scheduler."""

from __future__ import annotations

from typing import Any, Dict

from sqlalchemy.orm import Session

from src.core.config import get_settings
from src.core.metrics import record_replies_generated, record_reply_blocked
from src.domain.agents.pipeline import evaluate_candidate_bundle
from src.ingestion.open_calls import list_candidates, run_open_calls_ingestion
from src.integrations.x.service import get_workspace_x_access_token
from src.integrations.x.x_client import XClient


def run_workspace_pipeline(
    session: Session,
    *,
    workspace_id: str,
    x_client: XClient,
) -> Dict[str, Any]:
    """Run one workspace pipeline iteration without cross-tenant state."""

    settings = get_settings()
    access_token = get_workspace_x_access_token(session, workspace_id=workspace_id)
    if access_token is None:
        return {
            "status": "skipped",
            "reason": "x_oauth_missing",
            "ingested": 0,
            "evaluated_candidates": 0,
            "eligible_reply_candidates": 0,
        }

    ingestion = run_open_calls_ingestion(
        session,
        workspace_id=workspace_id,
        x_client=x_client,
        max_results=20,
    )

    candidates = list_candidates(
        session,
        workspace_id=workspace_id,
        limit=settings.scheduler_candidate_evaluation_limit,
    )

    eligible = 0
    if candidates:
        record_replies_generated(workspace_id=workspace_id, count=len(candidates))

    for candidate in candidates:
        bundle = evaluate_candidate_bundle(
            {
                "workspace_id": workspace_id,
                "source_tweet_id": candidate.source_tweet_id,
                "conversation_id": candidate.conversation_id,
                "author_id": candidate.author_id,
                "author_handle": candidate.author_handle,
                "text": candidate.text,
                "intent": candidate.intent,
                "opportunity_score": candidate.opportunity_score,
                "url": candidate.url,
            }
        )
        brand_ok = bool(bundle["brand_consistency"]["passed"])
        cringe_ok = bool(bundle["cringe_guard"]["passed"])
        if brand_ok and cringe_ok:
            eligible += 1
        else:
            if not brand_ok:
                record_reply_blocked(workspace_id=workspace_id, reason="brand_guard")
            if not cringe_ok:
                record_reply_blocked(workspace_id=workspace_id, reason="cringe_guard")

    return {
        "status": "executed",
        "ingested": ingestion.fetched,
        "stored_new": ingestion.stored_new,
        "stored_updated": ingestion.stored_updated,
        "ranked": ingestion.ranked,
        "top_opportunity_score": ingestion.top_opportunity_score,
        "evaluated_candidates": len(candidates),
        "eligible_reply_candidates": eligible,
    }
