"""Read-only open-calls ingestion pipeline for X."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from src.integrations.x.service import get_workspace_x_access_token
from src.integrations.x.x_client import XClient, XClientError
from src.storage.models import IngestionCandidate, WorkspaceEvent


OPEN_CALL_PATTERNS = [
    "drop your saas",
    "share your startup",
    "what are you building",
    "show your product",
    "open thread",
    "builders",
    "founders",
    "promote your saas",
]


@dataclass(frozen=True)
class OpenCallRunResult:
    fetched: int
    stored_new: int
    stored_updated: int
    ranked: int
    top_opportunity_score: int


def classify_intent(text: str) -> str:
    normalized = text.lower().strip()
    if any(pattern in normalized for pattern in OPEN_CALL_PATTERNS):
        return "open_call"
    if "?" in normalized:
        return "discussion"
    return "general"


def score_opportunity(text: str, public_metrics: Optional[Dict[str, Any]]) -> int:
    score = 10
    lowered = text.lower()
    intent = classify_intent(text)
    if intent == "open_call":
        score += 45
    elif intent == "discussion":
        score += 20

    for keyword in ["saas", "startup", "founder", "builder", "mrr", "revenue"]:
        if keyword in lowered:
            score += 5

    metrics = public_metrics or {}
    like_count = int(metrics.get("like_count") or 0)
    reply_count = int(metrics.get("reply_count") or 0)
    retweet_count = int(metrics.get("retweet_count") or 0)
    score += min(like_count // 5, 10)
    score += min(reply_count * 2, 12)
    score += min(retweet_count, 8)

    return max(0, min(score, 100))


def extract_candidates_from_search(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    tweets = payload.get("data") or []
    includes = payload.get("includes") or {}
    users = includes.get("users") or []
    usernames_by_id = {
        str(user.get("id")): str(user.get("username"))
        for user in users
        if isinstance(user, dict) and user.get("id") and user.get("username")
    }

    records: List[Dict[str, Any]] = []
    for tweet in tweets:
        if not isinstance(tweet, dict):
            continue
        tweet_id = str(tweet.get("id") or "")
        text = str(tweet.get("text") or "").strip()
        if not tweet_id or not text:
            continue

        author_id = str(tweet.get("author_id")) if tweet.get("author_id") else None
        author_handle = usernames_by_id.get(author_id) if author_id else None
        intent = classify_intent(text)
        opportunity_score = score_opportunity(text, tweet.get("public_metrics"))
        url = None
        if author_handle:
            url = f"https://x.com/{author_handle}/status/{tweet_id}"

        records.append(
            {
                "source_tweet_id": tweet_id,
                "conversation_id": str(tweet.get("conversation_id")) if tweet.get("conversation_id") else None,
                "author_id": author_id,
                "author_handle": author_handle,
                "text": text,
                "language": str(tweet.get("lang")) if tweet.get("lang") else None,
                "url": url,
                "intent": intent,
                "opportunity_score": opportunity_score,
                "raw_json": json.dumps(tweet, separators=(",", ":"), ensure_ascii=True, sort_keys=True),
            }
        )
    return records


def upsert_candidates(
    session: Session,
    *,
    workspace_id: str,
    source: str,
    records: List[Dict[str, Any]],
) -> Tuple[int, int]:
    stored_new = 0
    stored_updated = 0
    now = datetime.now(timezone.utc)

    for record in records:
        existing = session.scalar(
            select(IngestionCandidate).where(
                IngestionCandidate.workspace_id == workspace_id,
                IngestionCandidate.source == source,
                IngestionCandidate.source_tweet_id == record["source_tweet_id"],
            )
        )
        if existing is None:
            candidate = IngestionCandidate(
                workspace_id=workspace_id,
                source=source,
                source_tweet_id=record["source_tweet_id"],
                conversation_id=record["conversation_id"],
                author_id=record["author_id"],
                author_handle=record["author_handle"],
                text=record["text"],
                language=record["language"],
                url=record["url"],
                intent=record["intent"],
                opportunity_score=record["opportunity_score"],
                status="ingested",
                raw_json=record["raw_json"],
            )
            session.add(candidate)
            stored_new += 1
        else:
            existing.conversation_id = record["conversation_id"]
            existing.author_id = record["author_id"]
            existing.author_handle = record["author_handle"]
            existing.text = record["text"]
            existing.language = record["language"]
            existing.url = record["url"]
            existing.intent = record["intent"]
            existing.opportunity_score = record["opportunity_score"]
            existing.raw_json = record["raw_json"]
            existing.status = "ingested"
            existing.updated_at = now
            stored_updated += 1

    return stored_new, stored_updated


def run_open_calls_ingestion(
    session: Session,
    *,
    workspace_id: str,
    x_client: XClient,
    max_results: int = 20,
    query: Optional[str] = None,
) -> OpenCallRunResult:
    access_token = get_workspace_x_access_token(session, workspace_id=workspace_id)
    if access_token is None:
        raise RuntimeError("Workspace is not connected to X OAuth")

    try:
        payload = x_client.search_open_calls(
            access_token=access_token,
            query=query,
            max_results=max_results,
        )
    except XClientError as exc:
        raise RuntimeError(str(exc)) from exc

    records = extract_candidates_from_search(payload)
    stored_new, stored_updated = upsert_candidates(
        session,
        workspace_id=workspace_id,
        source="x",
        records=records,
    )

    ranked = sum(1 for record in records if record["intent"] == "open_call")
    top_score = max((record["opportunity_score"] for record in records), default=0)

    event_payload = {
        "fetched": len(records),
        "stored_new": stored_new,
        "stored_updated": stored_updated,
        "ranked": ranked,
        "top_opportunity_score": top_score,
        "query": query or x_client.default_open_calls_query,
    }
    session.add(
        WorkspaceEvent(
            workspace_id=workspace_id,
            event_type="ingestion_open_calls_run",
            payload_json=json.dumps(event_payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True),
        )
    )
    session.commit()

    return OpenCallRunResult(
        fetched=len(records),
        stored_new=stored_new,
        stored_updated=stored_updated,
        ranked=ranked,
        top_opportunity_score=top_score,
    )


def list_candidates(
    session: Session,
    *,
    workspace_id: str,
    limit: int = 20,
) -> List[IngestionCandidate]:
    safe_limit = max(1, min(limit, 100))
    statement = (
        select(IngestionCandidate)
        .where(IngestionCandidate.workspace_id == workspace_id)
        .order_by(desc(IngestionCandidate.opportunity_score), desc(IngestionCandidate.created_at))
        .limit(safe_limit)
    )
    return list(session.scalars(statement).all())

