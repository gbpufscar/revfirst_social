"""Pipeline step: classify and rank opportunity candidates."""

from __future__ import annotations

from typing import Any

from agents.intent_classifier.classifier import classify_intent
from agents.opportunity_scorer.scorer import score_opportunity
from config.settings import get_settings
from orchestrator.storage import (
    fetch_candidates,
    get_connection,
    init_db,
    record_pipeline_run,
    upsert_ranked_candidates,
)
from pipelines.io_utils import build_data_path, write_json


def _estimate_audience_fit(text: str) -> float:
    lowered = text.lower()
    markers = ["b2b", "saas", "founder", "growth", "pipeline", "product", "lead"]
    matched = sum(1 for marker in markers if marker in lowered)
    return min(1.0, 0.45 + matched * 0.1)


def _estimate_urgency(text: str) -> float:
    lowered = text.lower()
    markers = ["need", "now", "asap", "today", "stuck", "struggling", "urgent"]
    matched = sum(1 for marker in markers if marker in lowered)
    return min(1.0, 0.35 + matched * 0.12)


def run(limit: int = 100) -> dict:
    settings = get_settings()
    artifact_path = build_data_path(settings.data_dir, "ranked_candidates.json")

    details: dict[str, Any] = {
        "candidates_seen": 0,
        "ranked": 0,
        "top_score": 0.0,
        "artifact": str(artifact_path),
    }

    conn = get_connection(settings.db_path)
    try:
        init_db(conn)
        candidates = fetch_candidates(conn, limit=limit)
        details["candidates_seen"] = len(candidates)

        ranked_rows: list[dict[str, Any]] = []
        for candidate in candidates:
            intent = classify_intent(candidate["text"])
            audience_fit = _estimate_audience_fit(candidate["text"])
            urgency = _estimate_urgency(candidate["text"])
            opportunity_score = score_opportunity(
                intent_confidence=float(intent.confidence),
                audience_fit=audience_fit,
                urgency=urgency,
            )

            ranked_rows.append(
                {
                    "post_id": candidate["post_id"],
                    "author": candidate["author"],
                    "text": candidate["text"],
                    "created_at": candidate["created_at"],
                    "intent_label": intent.label,
                    "intent_confidence": float(intent.confidence),
                    "audience_fit": audience_fit,
                    "urgency": urgency,
                    "opportunity_score": opportunity_score,
                }
            )

        ranked_rows.sort(key=lambda row: row["opportunity_score"], reverse=True)
        stored_count = upsert_ranked_candidates(conn, ranked_rows)
        write_json(artifact_path, ranked_rows)

        details["ranked"] = stored_count
        details["top_score"] = float(ranked_rows[0]["opportunity_score"]) if ranked_rows else 0.0
        record_pipeline_run(conn, "rank_candidates", "ok", details)
    finally:
        conn.close()

    return {"status": "ok", "pipeline": "rank_candidates", **details}
