"""Pipeline step: generate and validate reply proposals."""

from __future__ import annotations

from typing import Any

from agents.anti_cringe_guard.cringe_detector import detect_cringe
from agents.brand_consistency_agent.validator import validate_brand_voice
from agents.reply_writer.writer import write_reply
from config.settings import get_settings
from orchestrator.storage import (
    fetch_ranked_candidates,
    get_connection,
    init_db,
    record_pipeline_run,
    upsert_proposed_replies,
)
from pipelines.io_utils import build_data_path, write_jsonl


def _build_context(author: str, text: str) -> str:
    sanitized = " ".join(text.split())
    return f"{author} asked: {sanitized}"


def _quality_score(
    opportunity_score: float,
    brand_ok: bool,
    cringe_ok: bool,
    issue_count: int,
) -> float:
    score = float(opportunity_score) + 0.08
    if not brand_ok:
        score -= 0.25
    if not cringe_ok:
        score -= 0.2
    score -= issue_count * 0.03
    return max(0.0, min(1.0, round(score, 4)))


def run(limit: int = 50, min_score: float = 0.6, approval_threshold: float = 0.7) -> dict:
    settings = get_settings()
    proposed_path = build_data_path(settings.data_dir, "proposed_replies.jsonl")
    approved_path = build_data_path(settings.data_dir, "approved_queue.jsonl")

    details: dict[str, Any] = {
        "candidates_seen": 0,
        "proposed": 0,
        "approved": 0,
        "artifact_proposed": str(proposed_path),
        "artifact_approved": str(approved_path),
    }

    conn = get_connection(settings.db_path)
    try:
        init_db(conn)
        ranked = fetch_ranked_candidates(conn, min_score=min_score, limit=limit)
        details["candidates_seen"] = len(ranked)

        proposed_rows: list[dict[str, Any]] = []
        approved_rows: list[dict[str, Any]] = []

        for candidate in ranked:
            if candidate["intent_label"] != "opportunity":
                continue

            reply_text = write_reply(_build_context(candidate["author"], candidate["text"]), max_chars=280)
            brand_ok, brand_issues = validate_brand_voice(reply_text)
            cringe_flag, cringe_issues = detect_cringe(reply_text)
            cringe_ok = not cringe_flag

            issues = [*brand_issues, *cringe_issues]
            quality_score = _quality_score(
                opportunity_score=float(candidate["opportunity_score"]),
                brand_ok=brand_ok,
                cringe_ok=cringe_ok,
                issue_count=len(issues),
            )

            row: dict[str, Any] = {
                "post_id": candidate["post_id"],
                "reply_to_id": candidate["post_id"],
                "reply_text": reply_text,
                "opportunity_score": float(candidate["opportunity_score"]),
                "quality_score": quality_score,
                "brand_ok": brand_ok,
                "cringe_ok": cringe_ok,
                "issues": issues,
            }
            proposed_rows.append(row)

            if brand_ok and cringe_ok and quality_score >= approval_threshold:
                approved_rows.append(row)

        stored_count = upsert_proposed_replies(conn, proposed_rows)
        write_jsonl(proposed_path, proposed_rows)
        write_jsonl(approved_path, approved_rows)

        details["proposed"] = stored_count
        details["approved"] = len(approved_rows)
        record_pipeline_run(conn, "propose_replies", "ok", details)
    finally:
        conn.close()

    return {"status": "ok", "pipeline": "propose_replies", **details}
