"""Composition helpers for domain-agent evaluation."""

from __future__ import annotations

from typing import Any, Dict, Mapping

from src.domain.agents.anti_cringe_guard import evaluate_cringe
from src.domain.agents.brand_consistency import validate_brand_consistency
from src.domain.agents.lead_tracker import track_lead_from_candidate
from src.domain.agents.reply_writer import generate_reply_from_candidate, reply_draft_to_content_object
from src.domain.agents.thread_detector import detect_thread_opportunity


def evaluate_candidate_bundle(candidate: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    reply = generate_reply_from_candidate(candidate)
    content_object = reply_draft_to_content_object(reply)
    brand = validate_brand_consistency(reply.text)
    cringe = evaluate_cringe(reply.text)
    thread = detect_thread_opportunity(candidate)
    lead = track_lead_from_candidate(candidate)

    return {
        "reply_draft": reply.model_dump(),
        "content_object": content_object.model_dump(),
        "brand_consistency": brand.model_dump(),
        "cringe_guard": cringe.model_dump(),
        "thread_detector": thread.model_dump(),
        "lead_tracker": lead.model_dump(),
    }
