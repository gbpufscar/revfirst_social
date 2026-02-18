"""Daily post generation using Telegram seeds with guard validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from src.core.config import get_settings
from src.core.metrics import record_daily_post_published, record_seed_used
from src.domain.agents.anti_cringe_guard import evaluate_cringe
from src.domain.agents.brand_consistency import validate_brand_consistency
from src.integrations.telegram.service import list_recent_telegram_seeds
from src.integrations.x.x_client import XClient
from src.publishing.service import publish_post
from src.storage.models import DailyPostDraft, TelegramSeed, WorkspaceEvent


_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "your",
    "you",
    "about",
    "have",
    "will",
    "are",
    "one",
    "each",
    "when",
    "what",
    "into",
    "they",
    "them",
    "their",
    "then",
    "just",
    "every",
}


@dataclass(frozen=True)
class DailyPostResult:
    workspace_id: str
    draft_id: str
    status: str
    text: str
    brand_passed: bool
    brand_score: int
    cringe_passed: bool
    cringe_risk_score: int
    published: bool
    external_post_id: Optional[str]
    seed_count: int
    message: str


def _json_dump(payload: Any) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True)


def _json_load_dict(payload: str) -> Dict[str, Any]:
    try:
        loaded = json.loads(payload)
        if isinstance(loaded, dict):
            return loaded
    except Exception:  # pragma: no cover
        pass
    return {}


def _extract_keywords(seeds: List[TelegramSeed], limit: int = 4) -> List[str]:
    counter: Dict[str, int] = {}
    for seed in seeds:
        for token in re.findall(r"[a-zA-Z]{3,}", seed.normalized_text.lower()):
            if token in _STOPWORDS:
                continue
            counter[token] = counter.get(token, 0) + 1

    ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    return [token for token, _ in ranked[:limit]]


def _build_style_memory(seeds: List[TelegramSeed]) -> Dict[str, Any]:
    if not seeds:
        return {
            "seed_count": 0,
            "average_seed_sentence_words": 0.0,
            "top_keywords": [],
            "sample_openers": [],
        }

    avg_sentence_words_samples: List[float] = []
    openers: List[str] = []
    for seed in seeds:
        style = _json_load_dict(seed.style_fingerprint_json)
        value = style.get("average_sentence_words")
        if isinstance(value, (int, float)):
            avg_sentence_words_samples.append(float(value))
        opener = style.get("opener")
        if isinstance(opener, str) and opener:
            openers.append(opener)

    avg_seed_sentence_words = 0.0
    if avg_sentence_words_samples:
        avg_seed_sentence_words = sum(avg_sentence_words_samples) / len(avg_sentence_words_samples)

    keywords = _extract_keywords(seeds)
    return {
        "seed_count": len(seeds),
        "average_seed_sentence_words": round(avg_seed_sentence_words, 2),
        "top_keywords": keywords,
        "sample_openers": openers[:3],
    }


def _clean_topic(topic: Optional[str]) -> str:
    settings = get_settings()
    resolved = (topic or settings.daily_post_default_topic).strip()
    return re.sub(r"\s+", " ", resolved)


def _compose_daily_post(*, topic: str, style_memory: Dict[str, Any]) -> str:
    topic_fragment = topic.lower()
    keywords = style_memory.get("top_keywords") or []
    keyword_fragment = "conversation signals"
    if len(keywords) >= 2:
        keyword_fragment = f"{keywords[0]} and {keywords[1]}"
    elif len(keywords) == 1:
        keyword_fragment = keywords[0]

    sentence_1 = f"Founder note on {topic_fragment}: builders keep revenue moving by shipping one small experiment weekly"
    sentence_2 = f"Use {keyword_fragment} to write a direct post, then answer every relevant founder reply"
    sentence_3 = "Measure profile visits, founder conversations, and trial starts before changing the playbook"
    return f"{sentence_1}. {sentence_2}. {sentence_3}."


def _create_draft(
    session: Session,
    *,
    workspace_id: str,
    topic: str,
    text: str,
    style_memory: Dict[str, Any],
    seed_ids: List[str],
    status: str,
    brand_score: int,
    brand_violations: List[str],
    cringe_risk_score: int,
    cringe_flags: List[str],
    publish_action: Optional[str],
    external_post_id: Optional[str],
    error_message: Optional[str],
) -> DailyPostDraft:
    draft = DailyPostDraft(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        topic=topic,
        content_text=text,
        style_memory_json=_json_dump(style_memory),
        seed_reference_ids_json=_json_dump(seed_ids),
        status=status,
        brand_score=brand_score,
        brand_violations_json=_json_dump(brand_violations),
        cringe_risk_score=cringe_risk_score,
        cringe_flags_json=_json_dump(cringe_flags),
        publish_action=publish_action,
        external_post_id=external_post_id,
        error_message=error_message,
    )
    session.add(draft)
    session.commit()
    return draft


def generate_daily_post(
    session: Session,
    *,
    workspace_id: str,
    topic: Optional[str],
    auto_publish: bool,
    x_client: XClient,
) -> DailyPostResult:
    settings = get_settings()
    seeds = list_recent_telegram_seeds(
        session,
        workspace_id=workspace_id,
        limit=settings.daily_post_seed_limit,
    )
    style_memory = _build_style_memory(seeds)
    if seeds:
        record_seed_used(workspace_id=workspace_id, count=len(seeds))

    resolved_topic = _clean_topic(topic)
    text = _compose_daily_post(topic=resolved_topic, style_memory=style_memory)

    brand = validate_brand_consistency(text)
    cringe = evaluate_cringe(text)

    if (not brand.passed) or cringe.cringe:
        draft = _create_draft(
            session,
            workspace_id=workspace_id,
            topic=resolved_topic,
            text=text,
            style_memory=style_memory,
            seed_ids=[seed.id for seed in seeds],
            status="blocked_guard",
            brand_score=brand.score,
            brand_violations=brand.violations,
            cringe_risk_score=cringe.risk_score,
            cringe_flags=cringe.flags,
            publish_action=None,
            external_post_id=None,
            error_message="Blocked by brand/cringe guards",
        )
        session.add(
            WorkspaceEvent(
                workspace_id=workspace_id,
                event_type="daily_post_blocked_guard",
                payload_json=_json_dump({"draft_id": draft.id}),
            )
        )
        session.commit()
        return DailyPostResult(
            workspace_id=workspace_id,
            draft_id=draft.id,
            status=draft.status,
            text=draft.content_text,
            brand_passed=brand.passed,
            brand_score=brand.score,
            cringe_passed=not cringe.cringe,
            cringe_risk_score=cringe.risk_score,
            published=False,
            external_post_id=None,
            seed_count=len(seeds),
            message="Daily post blocked by guards",
        )

    draft = _create_draft(
        session,
        workspace_id=workspace_id,
        topic=resolved_topic,
        text=text,
        style_memory=style_memory,
        seed_ids=[seed.id for seed in seeds],
        status="ready",
        brand_score=brand.score,
        brand_violations=brand.violations,
        cringe_risk_score=cringe.risk_score,
        cringe_flags=cringe.flags,
        publish_action=None,
        external_post_id=None,
        error_message=None,
    )

    status = draft.status
    published = False
    external_post_id: Optional[str] = None
    message = "Daily post generated and ready"
    if auto_publish:
        publish_result = publish_post(
            session,
            workspace_id=workspace_id,
            text=draft.content_text,
            x_client=x_client,
        )
        status = "published" if publish_result.published else publish_result.status
        published = publish_result.published
        external_post_id = publish_result.external_post_id
        message = publish_result.message

        fresh = session.scalar(
            select(DailyPostDraft).where(
                DailyPostDraft.id == draft.id,
                DailyPostDraft.workspace_id == workspace_id,
            )
        )
        if fresh is not None:
            fresh.status = status
            fresh.publish_action = "publish_post"
            fresh.external_post_id = external_post_id
            fresh.error_message = None if publish_result.published else publish_result.message
            fresh.updated_at = datetime.now(timezone.utc)
        session.commit()
        if publish_result.published:
            record_daily_post_published(workspace_id=workspace_id)

    event_type = "daily_post_published" if published else "daily_post_ready"
    if auto_publish and not published:
        event_type = "daily_post_publish_failed"
    session.add(
        WorkspaceEvent(
            workspace_id=workspace_id,
            event_type=event_type,
            payload_json=_json_dump(
                {
                    "draft_id": draft.id,
                    "status": status,
                    "published": published,
                    "external_post_id": external_post_id,
                }
            ),
        )
    )
    session.commit()

    return DailyPostResult(
        workspace_id=workspace_id,
        draft_id=draft.id,
        status=status,
        text=draft.content_text,
        brand_passed=brand.passed,
        brand_score=brand.score,
        cringe_passed=not cringe.cringe,
        cringe_risk_score=cringe.risk_score,
        published=published,
        external_post_id=external_post_id,
        seed_count=len(seeds),
        message=message,
    )


def list_daily_post_drafts(
    session: Session,
    *,
    workspace_id: str,
    limit: int = 20,
) -> List[DailyPostDraft]:
    safe_limit = max(1, min(limit, 100))
    statement = (
        select(DailyPostDraft)
        .where(DailyPostDraft.workspace_id == workspace_id)
        .order_by(desc(DailyPostDraft.created_at))
        .limit(safe_limit)
    )
    return list(session.scalars(statement).all())
