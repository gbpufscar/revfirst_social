"""Strategy agent that extracts growth patterns from benchmark X accounts."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from typing import Any, Dict, Optional
import uuid

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from src.integrations.x.service import get_workspace_x_access_token
from src.integrations.x.x_client import XClient
from src.storage.models import (
    WorkspaceEvent,
    XCompetitorPost,
    XStrategyPattern,
    XStrategyRecommendation,
    XStrategyWatchlist,
)


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True)


def _json_load(payload: str) -> Any:
    try:
        return json.loads(payload)
    except Exception:
        return {}


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return 0
        try:
            return int(stripped)
        except ValueError:
            return 0
    return 0


def _normalize_dt(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def upsert_watchlist_account(
    session: Session,
    *,
    workspace_id: str,
    account_user_id: str,
    account_username: Optional[str] = None,
    added_by_user_id: Optional[str] = None,
) -> XStrategyWatchlist:
    normalized_account_user_id = account_user_id.strip()
    if not normalized_account_user_id:
        raise ValueError("account_user_id_required")
    normalized_username = account_username.strip() if isinstance(account_username, str) else None
    if normalized_username == "":
        normalized_username = None

    row = session.scalar(
        select(XStrategyWatchlist).where(
            XStrategyWatchlist.workspace_id == workspace_id,
            XStrategyWatchlist.account_user_id == normalized_account_user_id,
        )
    )
    if row is None:
        row = XStrategyWatchlist(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            account_user_id=normalized_account_user_id,
            account_username=normalized_username,
            status="active",
            added_by_user_id=added_by_user_id,
        )
        session.add(row)
    else:
        row.account_username = normalized_username or row.account_username
        row.status = "active"
        row.updated_at = datetime.now(timezone.utc)

    session.commit()
    return row


def list_watchlist_accounts(session: Session, *, workspace_id: str, status: str = "active") -> list[XStrategyWatchlist]:
    normalized_status = status.strip().lower()
    return list(
        session.scalars(
            select(XStrategyWatchlist)
            .where(
                XStrategyWatchlist.workspace_id == workspace_id,
                XStrategyWatchlist.status == normalized_status,
            )
            .order_by(desc(XStrategyWatchlist.added_at))
        ).all()
    )


def _upsert_competitor_post(
    session: Session,
    *,
    workspace_id: str,
    watched_account_user_id: str,
    watched_account_username: Optional[str],
    payload: Dict[str, Any],
) -> bool:
    post_id = str(payload.get("id") or "").strip()
    text = str(payload.get("text") or "").strip()
    if not post_id or not text:
        return False

    metrics = payload.get("public_metrics")
    if not isinstance(metrics, dict):
        metrics = {}

    post_created_at = payload.get("created_at")
    normalized_post_created_at = None
    if isinstance(post_created_at, str) and post_created_at.strip():
        try:
            normalized_post_created_at = datetime.fromisoformat(post_created_at.replace("Z", "+00:00"))
        except ValueError:
            normalized_post_created_at = None

    existing = session.scalar(
        select(XCompetitorPost).where(
            XCompetitorPost.workspace_id == workspace_id,
            XCompetitorPost.watched_account_user_id == watched_account_user_id,
            XCompetitorPost.external_post_id == post_id,
        )
    )
    if existing is None:
        existing = XCompetitorPost(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            watched_account_user_id=watched_account_user_id,
            watched_account_username=watched_account_username,
            external_post_id=post_id,
            text=text,
            post_created_at=normalized_post_created_at,
            like_count=_as_int(metrics.get("like_count")),
            reply_count=_as_int(metrics.get("reply_count")),
            repost_count=_as_int(metrics.get("retweet_count") or metrics.get("repost_count")),
            quote_count=_as_int(metrics.get("quote_count")),
            impression_count=_as_int(metrics.get("impression_count")) or None,
            has_image=bool(payload.get("has_image")),
            raw_json=_json_dumps(payload.get("raw") if isinstance(payload.get("raw"), dict) else payload),
        )
        session.add(existing)
    else:
        existing.text = text
        existing.watched_account_username = watched_account_username or existing.watched_account_username
        existing.post_created_at = normalized_post_created_at
        existing.like_count = _as_int(metrics.get("like_count"))
        existing.reply_count = _as_int(metrics.get("reply_count"))
        existing.repost_count = _as_int(metrics.get("retweet_count") or metrics.get("repost_count"))
        existing.quote_count = _as_int(metrics.get("quote_count"))
        existing.impression_count = _as_int(metrics.get("impression_count")) or None
        existing.has_image = bool(payload.get("has_image"))
        existing.raw_json = _json_dumps(payload.get("raw") if isinstance(payload.get("raw"), dict) else payload)
        existing.captured_at = datetime.now(timezone.utc)
        existing.updated_at = datetime.now(timezone.utc)
    return True


def _compute_pattern(rows: list[XCompetitorPost], *, window_days: int) -> Dict[str, Any]:
    total_posts = len(rows)
    posts_per_day = round(total_posts / max(1, window_days), 2)
    avg_text_length = round(sum(len(row.text) for row in rows) / total_posts, 2) if rows else 0.0

    image_rows = [row for row in rows if row.has_image is not None]
    image_rate = 0.0
    if image_rows:
        image_rate = round(sum(1 for row in image_rows if row.has_image) / len(image_rows), 2)

    engagement_values = [row.like_count + row.reply_count + row.repost_count + row.quote_count for row in rows]
    avg_engagement = round(sum(engagement_values) / total_posts, 2) if total_posts else 0.0

    openers = Counter()
    hour_counter = Counter()
    for row in rows:
        opener = " ".join(row.text.lower().split()[:4]).strip()
        if opener:
            openers[opener] += 1
        if row.post_created_at is not None:
            hour_counter[_normalize_dt(row.post_created_at).hour] += 1

    top_openers = [entry for entry, _ in openers.most_common(5)]
    best_hours_utc = [hour for hour, _ in hour_counter.most_common(3)]
    account_count = len({row.watched_account_user_id for row in rows})

    return {
        "window_days": window_days,
        "accounts_analyzed": account_count,
        "total_posts": total_posts,
        "posts_per_day": posts_per_day,
        "avg_text_length": avg_text_length,
        "image_rate": image_rate,
        "avg_engagement": avg_engagement,
        "top_openers": top_openers,
        "best_hours_utc": best_hours_utc,
    }


def _build_recommendations(pattern: Dict[str, Any]) -> list[str]:
    recommendations: list[str] = []
    posts_per_day = float(pattern.get("posts_per_day") or 0.0)
    image_rate = float(pattern.get("image_rate") or 0.0)
    avg_engagement = float(pattern.get("avg_engagement") or 0.0)
    top_openers = pattern.get("top_openers") if isinstance(pattern.get("top_openers"), list) else []
    best_hours = pattern.get("best_hours_utc") if isinstance(pattern.get("best_hours_utc"), list) else []

    if posts_per_day >= 1.0:
        recommendations.append("Manter cadencia proxima de 1 post/dia no X para consistencia de alcance.")
    else:
        recommendations.append("Aumentar cadencia de publicacao para reduzir janelas sem distribuicao.")

    if image_rate >= 0.4:
        recommendations.append("Incluir mais posts com imagem para capturar padrao de contas com maior tracao.")
    else:
        recommendations.append("Priorizar copy clara e curta; testar imagem apenas em posts de maior potencial.")

    if avg_engagement >= 10:
        recommendations.append("Reforcar estrategia de CTA para resposta, aproveitando bom baseline de engajamento.")
    else:
        recommendations.append("Ajustar hooks de abertura e iterar temas com mais dor explicita de founders.")

    if top_openers:
        recommendations.append(f"Testar abertura inspirada em: '{top_openers[0]}'.")
    if best_hours:
        recommendations.append(f"Priorizar janela UTC: {', '.join(str(value) for value in best_hours)}.")

    return recommendations


def run_workspace_strategy_scan(
    session: Session,
    *,
    workspace_id: str,
    x_client: XClient,
    max_posts_per_account: int = 20,
    window_days: int = 14,
) -> Dict[str, Any]:
    token = get_workspace_x_access_token(session, workspace_id=workspace_id, x_client=x_client)
    if token is None:
        return {
            "workspace_id": workspace_id,
            "status": "missing_x_oauth",
            "watchlist_count": 0,
            "ingested_posts": 0,
            "errors": ["x_oauth_missing_or_expired"],
        }

    watchlist = list_watchlist_accounts(session, workspace_id=workspace_id, status="active")
    if not watchlist:
        return {
            "workspace_id": workspace_id,
            "status": "no_watchlist",
            "watchlist_count": 0,
            "ingested_posts": 0,
            "errors": [],
        }

    ingested_posts = 0
    errors: list[str] = []
    for account in watchlist:
        try:
            posts = x_client.get_user_recent_posts(
                access_token=token,
                user_id=account.account_user_id,
                max_results=max_posts_per_account,
            )
        except Exception:
            errors.append(f"user_scan_failed:{account.account_user_id}")
            continue

        for payload in posts:
            created = _upsert_competitor_post(
                session,
                workspace_id=workspace_id,
                watched_account_user_id=account.account_user_id,
                watched_account_username=account.account_username,
                payload=payload,
            )
            if created:
                ingested_posts += 1

    window_start = datetime.now(timezone.utc) - timedelta(days=max(1, window_days))
    rows = list(
        session.scalars(
            select(XCompetitorPost).where(
                XCompetitorPost.workspace_id == workspace_id,
                XCompetitorPost.captured_at >= window_start,
            )
        ).all()
    )

    if not rows:
        session.add(
            WorkspaceEvent(
                workspace_id=workspace_id,
                event_type="x_strategy_scan_completed",
                payload_json=_json_dumps(
                    {
                        "status": "no_data",
                        "watchlist_count": len(watchlist),
                        "ingested_posts": ingested_posts,
                        "errors": errors,
                    }
                ),
            )
        )
        session.commit()
        return {
            "workspace_id": workspace_id,
            "status": "no_data",
            "watchlist_count": len(watchlist),
            "ingested_posts": ingested_posts,
            "errors": errors,
        }

    pattern_payload = _compute_pattern(rows, window_days=window_days)
    recommendations = _build_recommendations(pattern_payload)
    confidence_score = min(100, int(pattern_payload.get("total_posts", 0) * 3))

    pattern_row = XStrategyPattern(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        period_window=f"{window_days}d",
        pattern_json=_json_dumps(pattern_payload),
        confidence_score=confidence_score,
    )
    recommendations_row = XStrategyRecommendation(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        period_window=f"{window_days}d",
        recommendation_json=_json_dumps({"items": recommendations}),
        rationale_json=_json_dumps(
            {
                "total_posts": pattern_payload.get("total_posts"),
                "accounts_analyzed": pattern_payload.get("accounts_analyzed"),
                "confidence_score": confidence_score,
            }
        ),
    )
    session.add(pattern_row)
    session.add(recommendations_row)
    session.add(
        WorkspaceEvent(
            workspace_id=workspace_id,
            event_type="x_strategy_scan_completed",
            payload_json=_json_dumps(
                {
                    "status": "scanned",
                    "watchlist_count": len(watchlist),
                    "ingested_posts": ingested_posts,
                    "pattern_id": pattern_row.id,
                    "recommendation_id": recommendations_row.id,
                    "errors": errors,
                }
            ),
        )
    )
    session.commit()

    return {
        "workspace_id": workspace_id,
        "status": "scanned",
        "watchlist_count": len(watchlist),
        "ingested_posts": ingested_posts,
        "pattern": pattern_payload,
        "recommendations": recommendations,
        "confidence_score": confidence_score,
        "errors": errors,
    }


def latest_workspace_strategy_report(session: Session, *, workspace_id: str) -> Dict[str, Any]:
    pattern_row = session.scalar(
        select(XStrategyPattern)
        .where(XStrategyPattern.workspace_id == workspace_id)
        .order_by(XStrategyPattern.generated_at.desc())
        .limit(1)
    )
    recommendation_row = session.scalar(
        select(XStrategyRecommendation)
        .where(XStrategyRecommendation.workspace_id == workspace_id)
        .order_by(XStrategyRecommendation.created_at.desc())
        .limit(1)
    )
    watchlist_count = len(list_watchlist_accounts(session, workspace_id=workspace_id, status="active"))

    if pattern_row is None and recommendation_row is None:
        return {
            "workspace_id": workspace_id,
            "available": False,
            "watchlist_count": watchlist_count,
        }

    recommendation_payload = {}
    if recommendation_row is not None:
        loaded = _json_load(recommendation_row.recommendation_json)
        if isinstance(loaded, dict):
            recommendation_payload = loaded

    return {
        "workspace_id": workspace_id,
        "available": True,
        "watchlist_count": watchlist_count,
        "period_window": pattern_row.period_window if pattern_row is not None else recommendation_row.period_window,
        "pattern": _json_load(pattern_row.pattern_json) if pattern_row is not None else {},
        "confidence_score": pattern_row.confidence_score if pattern_row is not None else 0,
        "recommendations": recommendation_payload.get("items", []),
        "generated_at": _normalize_dt(pattern_row.generated_at).isoformat() if pattern_row is not None else None,
    }

