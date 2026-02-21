"""X performance and growth analytics agent."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from typing import Any, Dict, Optional
import uuid

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from src.integrations.x.service import get_workspace_x_access_token, get_workspace_x_connection_status
from src.integrations.x.x_client import XClient
from src.storage.models import PublishAuditLog, WorkspaceEvent, XAccountSnapshot, XGrowthInsight, XPostMetricsSnapshot


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True)


def _json_load_dict(payload: str) -> Dict[str, Any]:
    try:
        loaded = json.loads(payload)
    except Exception:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return loaded


def _as_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            return None
    return None


def _normalize_dt(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _published_post_ids(session: Session, *, workspace_id: str, limit: int) -> list[str]:
    rows = session.scalars(
        select(PublishAuditLog.external_post_id)
        .where(
            PublishAuditLog.workspace_id == workspace_id,
            PublishAuditLog.platform == "x",
            PublishAuditLog.status == "published",
            PublishAuditLog.external_post_id.is_not(None),
        )
        .order_by(desc(PublishAuditLog.created_at))
        .limit(max(1, limit))
    ).all()
    seen: set[str] = set()
    ordered: list[str] = []
    for raw_value in rows:
        value = str(raw_value or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _average(values: list[int]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 2)


def collect_workspace_growth_snapshot(
    session: Session,
    *,
    workspace_id: str,
    x_client: XClient,
    max_posts: int = 20,
) -> Dict[str, Any]:
    connection = get_workspace_x_connection_status(session, workspace_id=workspace_id)
    token = get_workspace_x_access_token(session, workspace_id=workspace_id, x_client=x_client)
    account_user_id = str(connection.get("account_user_id") or "").strip() or None
    account_username = str(connection.get("account_username") or "").strip() or None

    account_metrics: Dict[str, Any] = {}
    errors: list[str] = []
    if token and account_user_id:
        try:
            account_metrics = x_client.get_user_public_metrics(
                access_token=token,
                user_id=account_user_id,
            )
        except Exception:
            errors.append("account_metrics_unavailable")
    else:
        errors.append("x_oauth_missing_or_disconnected")

    public_metrics = account_metrics.get("public_metrics") if isinstance(account_metrics, dict) else {}
    if not isinstance(public_metrics, dict):
        public_metrics = {}

    snapshot = XAccountSnapshot(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        account_user_id=account_user_id,
        account_username=account_username,
        followers_count=_as_int(public_metrics.get("followers_count")),
        following_count=_as_int(public_metrics.get("following_count")),
        tweet_count=_as_int(public_metrics.get("tweet_count")),
        listed_count=_as_int(public_metrics.get("listed_count")),
    )
    session.add(snapshot)

    post_snapshots = 0
    if token:
        for post_id in _published_post_ids(session, workspace_id=workspace_id, limit=max_posts):
            try:
                post_metrics = x_client.get_tweet_public_metrics(
                    access_token=token,
                    tweet_id=post_id,
                )
            except Exception:
                errors.append(f"post_metrics_unavailable:{post_id}")
                continue

            metrics = post_metrics.get("public_metrics")
            if not isinstance(metrics, dict):
                metrics = {}

            session.add(
                XPostMetricsSnapshot(
                    id=str(uuid.uuid4()),
                    workspace_id=workspace_id,
                    external_post_id=post_id,
                    like_count=_as_int(metrics.get("like_count")),
                    reply_count=_as_int(metrics.get("reply_count")),
                    repost_count=_as_int(metrics.get("retweet_count") or metrics.get("repost_count")),
                    quote_count=_as_int(metrics.get("quote_count")),
                    bookmark_count=_as_int(metrics.get("bookmark_count")),
                    impression_count=_as_int(metrics.get("impression_count")),
                    has_image=bool(post_metrics.get("has_image")),
                )
            )
            post_snapshots += 1

    session.add(
        WorkspaceEvent(
            workspace_id=workspace_id,
            event_type="x_growth_snapshot_collected",
            payload_json=_json_dumps(
                {
                    "snapshot_id": snapshot.id,
                    "post_snapshots": post_snapshots,
                    "errors": errors,
                }
            ),
        )
    )
    session.commit()

    return {
        "workspace_id": workspace_id,
        "snapshot_id": snapshot.id,
        "post_snapshots": post_snapshots,
        "errors": errors,
        "connected": bool(connection.get("connected")),
        "account_user_id": account_user_id,
        "account_username": account_username,
    }


def _followers_delta(session: Session, *, workspace_id: str) -> Optional[int]:
    latest_two = list(
        session.scalars(
            select(XAccountSnapshot)
            .where(XAccountSnapshot.workspace_id == workspace_id)
            .order_by(XAccountSnapshot.captured_at.desc())
            .limit(2)
        ).all()
    )
    if len(latest_two) < 2:
        return None
    latest = latest_two[0]
    previous = latest_two[1]
    if latest.followers_count is None or previous.followers_count is None:
        return None
    return latest.followers_count - previous.followers_count


def _engagement_summary(
    session: Session,
    *,
    workspace_id: str,
    window_start: datetime,
) -> Dict[str, Any]:
    rows = list(
        session.scalars(
            select(XPostMetricsSnapshot).where(
                XPostMetricsSnapshot.workspace_id == workspace_id,
                XPostMetricsSnapshot.captured_at >= window_start,
            )
        ).all()
    )
    like_values = [row.like_count for row in rows if row.like_count is not None]
    reply_values = [row.reply_count for row in rows if row.reply_count is not None]
    repost_values = [row.repost_count for row in rows if row.repost_count is not None]
    quote_values = [row.quote_count for row in rows if row.quote_count is not None]
    impression_values = [row.impression_count for row in rows if row.impression_count is not None]

    return {
        "samples": len(rows),
        "avg_likes": _average(like_values),
        "avg_replies": _average(reply_values),
        "avg_reposts": _average(repost_values),
        "avg_quotes": _average(quote_values),
        "avg_impressions": _average(impression_values),
    }


def build_workspace_growth_report(
    session: Session,
    *,
    workspace_id: str,
    period_days: int = 1,
    persist_insight: bool = True,
) -> Dict[str, Any]:
    safe_period_days = max(1, min(period_days, 30))
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=safe_period_days)
    window_end = now

    publish_rows = list(
        session.scalars(
            select(PublishAuditLog).where(
                PublishAuditLog.workspace_id == workspace_id,
                PublishAuditLog.platform == "x",
                PublishAuditLog.created_at >= window_start,
                PublishAuditLog.created_at <= window_end,
            )
        ).all()
    )
    published_posts = sum(1 for row in publish_rows if row.action == "publish_post" and row.status == "published")
    published_replies = sum(1 for row in publish_rows if row.action == "publish_reply" and row.status == "published")
    failed_publications = sum(1 for row in publish_rows if row.status == "failed")

    follower_delta = _followers_delta(session, workspace_id=workspace_id)
    engagement = _engagement_summary(session, workspace_id=workspace_id, window_start=window_start)

    kpis: Dict[str, Any] = {
        "period_days": safe_period_days,
        "published_posts": published_posts,
        "published_replies": published_replies,
        "failed_publications": failed_publications,
        "follower_delta": follower_delta,
        "engagement": engagement,
    }

    recommendations: list[str] = []
    if failed_publications > 0:
        recommendations.append("Revisar erros de publicacao no X e status OAuth.")
    if published_posts < safe_period_days:
        recommendations.append("Aumentar cadencia de posts para ao menos 1 por dia.")
    if published_replies < max(3, safe_period_days * 2):
        recommendations.append("Aumentar volume de replies estrategicos em conversas de alto sinal.")
    if engagement.get("samples", 0) > 0 and engagement.get("avg_replies", 0.0) < 1.0:
        recommendations.append("Testar hooks mais diretos e CTAs de conversa para elevar respostas.")
    if follower_delta is not None and follower_delta <= 0:
        recommendations.append("Ajustar janela de horarios e sequencia de conteudo para recuperar crescimento.")
    if not recommendations:
        recommendations.append("Operacao estavel; manter cadencia e continuar monitorando crescimento.")

    report = {
        "workspace_id": workspace_id,
        "period_days": safe_period_days,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "kpis": kpis,
        "recommendations": recommendations,
    }

    if persist_insight:
        insight = XGrowthInsight(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            period_type="daily" if safe_period_days <= 1 else "weekly",
            period_start=date.fromisoformat(window_start.date().isoformat()),
            period_end=date.fromisoformat(window_end.date().isoformat()),
            kpis_json=_json_dumps(kpis),
            recommendations_json=_json_dumps(recommendations),
        )
        session.add(insight)
        session.add(
            WorkspaceEvent(
                workspace_id=workspace_id,
                event_type="x_growth_report_generated",
                payload_json=_json_dumps(
                    {
                        "period_days": safe_period_days,
                        "insight_id": insight.id,
                    }
                ),
            )
        )
        session.commit()

    return report


def latest_workspace_growth_insight(
    session: Session,
    *,
    workspace_id: str,
) -> Dict[str, Any]:
    row = session.scalar(
        select(XGrowthInsight)
        .where(XGrowthInsight.workspace_id == workspace_id)
        .order_by(XGrowthInsight.created_at.desc())
        .limit(1)
    )
    if row is None:
        return {"workspace_id": workspace_id, "available": False}

    return {
        "workspace_id": workspace_id,
        "available": True,
        "period_type": row.period_type,
        "period_start": row.period_start.isoformat(),
        "period_end": row.period_end.isoformat(),
        "kpis": _json_load_dict(row.kpis_json),
        "recommendations": _json_load_dict(row.recommendations_json)
        if row.recommendations_json.strip().startswith("{")
        else json.loads(row.recommendations_json or "[]"),
        "created_at": _normalize_dt(row.created_at).isoformat(),
    }

