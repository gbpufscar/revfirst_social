"""Strategy agent that extracts growth patterns from benchmark X accounts."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from typing import Any, Dict, Optional
import uuid

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from src.core.config import get_settings
from src.integrations.x.service import get_workspace_x_access_token
from src.integrations.x.x_client import XClient
from src.storage.models import (
    WorkspaceEvent,
    XCompetitorPost,
    XStrategyDiscoveryCandidate,
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


def _as_float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return 0.0
        try:
            return float(stripped)
        except ValueError:
            return 0.0
    return 0.0


def _normalize_dt(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def build_x_profile_url(*, account_user_id: str, account_username: Optional[str]) -> str:
    normalized_username = (account_username or "").strip().lstrip("@")
    if normalized_username:
        return f"https://x.com/{normalized_username}"
    normalized_user_id = account_user_id.strip()
    if normalized_user_id:
        return f"https://x.com/i/user/{normalized_user_id}"
    return "https://x.com"


def get_strategy_discovery_criteria(*, settings: Any | None = None) -> Dict[str, Any]:
    active_settings = settings if settings is not None else get_settings()
    min_followers = max(0, _as_int(getattr(active_settings, "x_strategy_candidate_min_followers", 0)))
    max_followers = max(min_followers, _as_int(getattr(active_settings, "x_strategy_candidate_max_followers", 0)))
    return {
        "min_score": max(0, min(100, _as_int(getattr(active_settings, "x_strategy_candidate_min_score", 0)))),
        "min_avg_engagement": max(0.0, _as_float(getattr(active_settings, "x_strategy_candidate_min_avg_engagement", 0.0))),
        "min_engagement_rate_pct": max(
            0.0,
            _as_float(getattr(active_settings, "x_strategy_candidate_min_engagement_rate_pct", 0.0)),
        ),
        "min_cadence_per_day": max(0.0, _as_float(getattr(active_settings, "x_strategy_candidate_min_cadence_per_day", 0.0))),
        "min_signal_posts": max(0, _as_int(getattr(active_settings, "x_strategy_candidate_min_signal_posts", 0))),
        "min_recent_posts": max(1, _as_int(getattr(active_settings, "x_strategy_candidate_min_recent_posts", 1))),
        "min_followers": min_followers,
        "max_followers": max_followers,
        "require_followers_in_band": bool(
            getattr(active_settings, "x_strategy_candidate_require_followers_in_band", True)
        ),
    }


def parse_discovery_candidate_rationale(candidate: XStrategyDiscoveryCandidate) -> Dict[str, Any]:
    raw = getattr(candidate, "rationale_json", "")
    if not isinstance(raw, str) or not raw.strip():
        return {}
    loaded = _json_load(raw)
    return loaded if isinstance(loaded, dict) else {}


def _score_discovery_candidate(
    *,
    followers_count: int,
    avg_engagement: float,
    cadence_per_day: float,
    signal_posts: int,
    min_followers: int,
    max_followers: int,
) -> tuple[int, Dict[str, Any]]:
    follower_band_points = 0
    if followers_count >= min_followers and followers_count <= max_followers:
        follower_band_points = 30
    elif followers_count > max_followers:
        follower_band_points = 10
    elif followers_count >= max(1, int(min_followers * 0.5)):
        follower_band_points = 15

    engagement_points = min(30, int(avg_engagement * 2))
    cadence_points = min(20, int(cadence_per_day * 20))
    signal_points = min(20, signal_posts * 5)
    score = min(100, follower_band_points + engagement_points + cadence_points + signal_points)

    rationale = {
        "follower_band_points": follower_band_points,
        "engagement_points": engagement_points,
        "cadence_points": cadence_points,
        "signal_points": signal_points,
        "followers_count": followers_count,
        "avg_engagement": round(avg_engagement, 2),
        "cadence_per_day": round(cadence_per_day, 2),
        "signal_posts": signal_posts,
        "score": score,
    }
    return score, rationale


def _calculate_recent_post_stats(posts: list[Dict[str, Any]]) -> Dict[str, Any]:
    if not posts:
        return {"avg_engagement": 0.0, "cadence_per_day": 0.0, "post_count": 0}

    engagement_samples: list[float] = []
    timestamps: list[datetime] = []
    for payload in posts:
        metrics = payload.get("public_metrics")
        if not isinstance(metrics, dict):
            metrics = {}
        engagement = float(
            _as_int(metrics.get("like_count"))
            + _as_int(metrics.get("reply_count"))
            + _as_int(metrics.get("retweet_count") or metrics.get("repost_count"))
            + _as_int(metrics.get("quote_count"))
        )
        engagement_samples.append(engagement)

        created_at = payload.get("created_at")
        if isinstance(created_at, str) and created_at.strip():
            try:
                timestamps.append(datetime.fromisoformat(created_at.replace("Z", "+00:00")))
            except ValueError:
                continue

    avg_engagement = round(sum(engagement_samples) / len(engagement_samples), 2) if engagement_samples else 0.0
    cadence_per_day = 0.0
    if len(timestamps) >= 2:
        min_dt = _normalize_dt(min(timestamps))
        max_dt = _normalize_dt(max(timestamps))
        window_days = max((max_dt - min_dt).total_seconds() / 86400.0, 1.0)
        cadence_per_day = round(len(timestamps) / window_days, 2)
    elif len(timestamps) == 1:
        cadence_per_day = 1.0

    return {
        "avg_engagement": avg_engagement,
        "cadence_per_day": cadence_per_day,
        "post_count": len(posts),
    }


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


def _upsert_discovery_candidate(
    session: Session,
    *,
    workspace_id: str,
    account_user_id: str,
    account_username: Optional[str],
    source_query: str,
    signal_post_count: int,
    followers_count: Optional[int],
    tweet_count: Optional[int],
    avg_engagement: float,
    cadence_per_day: float,
    score: int,
    rationale: Dict[str, Any],
    status: str = "pending",
) -> tuple[XStrategyDiscoveryCandidate, bool]:
    normalized_status = (status or "pending").strip().lower() or "pending"
    existing = session.scalar(
        select(XStrategyDiscoveryCandidate).where(
            XStrategyDiscoveryCandidate.workspace_id == workspace_id,
            XStrategyDiscoveryCandidate.account_user_id == account_user_id,
        )
    )
    if existing is None:
        row = XStrategyDiscoveryCandidate(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            account_user_id=account_user_id,
            account_username=account_username,
            source_query=source_query,
            signal_post_count=signal_post_count,
            followers_count=followers_count,
            tweet_count=tweet_count,
            avg_engagement=avg_engagement,
            cadence_per_day=cadence_per_day,
            score=score,
            rationale_json=_json_dumps(rationale),
            status=normalized_status,
        )
        session.add(row)
        return row, True

    existing.account_username = account_username or existing.account_username
    existing.source_query = source_query
    existing.signal_post_count = signal_post_count
    existing.followers_count = followers_count
    existing.tweet_count = tweet_count
    existing.avg_engagement = avg_engagement
    existing.cadence_per_day = cadence_per_day
    existing.score = score
    existing.rationale_json = _json_dumps(rationale)
    if existing.status not in {"approved", "rejected"}:
        existing.status = normalized_status
        if normalized_status == "pending":
            existing.reviewed_by_user_id = None
            existing.reviewed_at = None
    existing.discovered_at = datetime.now(timezone.utc)
    existing.updated_at = datetime.now(timezone.utc)
    return existing, False


def list_pending_strategy_candidates(
    session: Session,
    *,
    workspace_id: str,
    limit: int = 10,
) -> list[XStrategyDiscoveryCandidate]:
    return list(
        session.scalars(
            select(XStrategyDiscoveryCandidate)
            .where(
                XStrategyDiscoveryCandidate.workspace_id == workspace_id,
                XStrategyDiscoveryCandidate.status == "pending",
            )
            .order_by(
                XStrategyDiscoveryCandidate.score.desc(),
                XStrategyDiscoveryCandidate.discovered_at.desc(),
            )
            .limit(max(1, limit))
        ).all()
    )


def run_workspace_strategy_discovery(
    session: Session,
    *,
    workspace_id: str,
    x_client: XClient,
) -> Dict[str, Any]:
    settings = get_settings()
    token = get_workspace_x_access_token(session, workspace_id=workspace_id, x_client=x_client)
    if token is None:
        return {
            "workspace_id": workspace_id,
            "status": "missing_x_oauth",
            "pending_count": len(list_pending_strategy_candidates(session, workspace_id=workspace_id, limit=100)),
            "discovered": 0,
            "updated": 0,
            "errors": ["x_oauth_missing_or_expired"],
        }

    query = settings.x_strategy_discovery_query.strip() or settings.x_default_open_calls_query
    max_results = max(10, min(settings.x_strategy_discovery_max_results, 100))
    max_candidates = max(1, min(settings.x_strategy_discovery_max_candidates, 25))
    criteria = get_strategy_discovery_criteria(settings=settings)
    min_followers = int(criteria["min_followers"])
    max_followers = int(criteria["max_followers"])
    min_score = int(criteria["min_score"])
    min_avg_engagement = float(criteria["min_avg_engagement"])
    min_engagement_rate_pct = float(criteria["min_engagement_rate_pct"])
    min_cadence_per_day = float(criteria["min_cadence_per_day"])
    min_signal_posts = int(criteria["min_signal_posts"])
    min_recent_posts = int(criteria["min_recent_posts"])
    require_followers_in_band = bool(criteria["require_followers_in_band"])

    try:
        payload = x_client.search_open_calls(
            access_token=token,
            query=query,
            max_results=max_results,
        )
    except Exception:
        return {
            "workspace_id": workspace_id,
            "status": "search_failed",
            "pending_count": len(list_pending_strategy_candidates(session, workspace_id=workspace_id, limit=100)),
            "discovered": 0,
            "updated": 0,
            "errors": ["strategy_discovery_search_failed"],
        }

    users_raw: list[Dict[str, Any]] = []
    includes = payload.get("includes")
    if isinstance(includes, dict):
        candidate_users = includes.get("users")
        if isinstance(candidate_users, list):
            users_raw = [row for row in candidate_users if isinstance(row, dict)]

    signal_posts_by_author: Dict[str, int] = {}
    data_rows = payload.get("data")
    if isinstance(data_rows, list):
        for row in data_rows:
            if not isinstance(row, dict):
                continue
            author_id = str(row.get("author_id") or "").strip()
            if not author_id:
                continue
            signal_posts_by_author[author_id] = signal_posts_by_author.get(author_id, 0) + 1

    active_watchlist = list_watchlist_accounts(session, workspace_id=workspace_id, status="active")
    active_watchlist_ids = {row.account_user_id for row in active_watchlist}

    discovered = 0
    updated = 0
    scanned_users = 0
    quality_rejected = 0
    pruned_pending = 0
    errors: list[str] = []
    ranked_ids: list[str] = []
    rejected_by_reason: Counter[str] = Counter()
    evaluated_candidates: list[Dict[str, Any]] = []
    selected_candidates: list[Dict[str, Any]] = []

    dedupe_users: Dict[str, Dict[str, Any]] = {}
    for user in users_raw:
        user_id = str(user.get("id") or "").strip()
        if not user_id:
            continue
        dedupe_users[user_id] = user

    for user_id, user_payload in dedupe_users.items():
        if user_id in active_watchlist_ids:
            continue
        scanned_users += 1
        username = str(user_payload.get("username") or "").strip() or None

        try:
            metrics_payload = x_client.get_user_public_metrics(
                access_token=token,
                user_id=user_id,
            )
        except Exception:
            errors.append(f"user_metrics_failed:{user_id}")
            continue

        metrics = metrics_payload.get("public_metrics")
        if not isinstance(metrics, dict):
            metrics = {}
        followers_count = _as_int(metrics.get("followers_count"))
        tweet_count = _as_int(metrics.get("tweet_count"))
        if followers_count < min_followers:
            rejected_by_reason["min_followers"] += 1
            continue

        try:
            posts = x_client.get_user_recent_posts(
                access_token=token,
                user_id=user_id,
                max_results=15,
            )
        except Exception:
            errors.append(f"user_posts_failed:{user_id}")
            continue

        post_stats = _calculate_recent_post_stats(posts)
        signal_post_count = signal_posts_by_author.get(user_id, 0)
        avg_engagement = _as_float(post_stats.get("avg_engagement"))
        cadence_per_day = _as_float(post_stats.get("cadence_per_day"))
        post_count = int(post_stats.get("post_count") or 0)
        engagement_rate_pct = round((avg_engagement / max(1, followers_count)) * 100.0, 2)
        score, rationale = _score_discovery_candidate(
            followers_count=followers_count,
            avg_engagement=avg_engagement,
            cadence_per_day=cadence_per_day,
            signal_posts=signal_post_count,
            min_followers=min_followers,
            max_followers=max_followers,
        )
        profile_url = build_x_profile_url(account_user_id=user_id, account_username=username)

        quality_checks: Dict[str, bool] = {
            "score": score >= min_score,
            "avg_engagement": avg_engagement >= min_avg_engagement,
            "engagement_rate_pct": engagement_rate_pct >= min_engagement_rate_pct,
            "cadence_per_day": cadence_per_day >= min_cadence_per_day,
            "signal_post_count": signal_post_count >= min_signal_posts,
            "recent_posts": post_count >= min_recent_posts,
        }
        if require_followers_in_band:
            quality_checks["followers_in_band"] = followers_count <= max_followers
        failed_checks = [name for name, passed in quality_checks.items() if not passed]

        rationale["signal_post_count"] = signal_post_count
        rationale["post_count"] = post_count
        rationale["engagement_rate_pct"] = engagement_rate_pct
        rationale["profile_url"] = profile_url
        rationale["criteria"] = criteria
        rationale["quality_checks"] = quality_checks
        rationale["failed_checks"] = failed_checks
        rationale["quality_passed"] = not failed_checks

        if failed_checks:
            quality_rejected += 1
            for failed_reason in failed_checks:
                rejected_by_reason[failed_reason] += 1
            continue

        rationale["selection_reason"] = (
            f"score={score}; engagement={avg_engagement:.1f}; rate={engagement_rate_pct:.2f}%; "
            f"cadence={cadence_per_day:.2f}/day; signal_posts={signal_post_count}"
        )
        evaluated_candidates.append(
            {
                "account_user_id": user_id,
                "account_username": username,
                "followers_count": followers_count,
                "tweet_count": tweet_count,
                "avg_engagement": avg_engagement,
                "cadence_per_day": cadence_per_day,
                "signal_post_count": signal_post_count,
                "post_count": post_count,
                "engagement_rate_pct": engagement_rate_pct,
                "score": score,
                "profile_url": profile_url,
                "rationale": rationale,
            }
        )

    evaluated_candidates.sort(
        key=lambda entry: (
            int(entry.get("score") or 0),
            float(entry.get("engagement_rate_pct") or 0.0),
            int(entry.get("signal_post_count") or 0),
        ),
        reverse=True,
    )
    shortlisted = evaluated_candidates[:max_candidates]
    dropped_by_rank = max(0, len(evaluated_candidates) - len(shortlisted))
    if dropped_by_rank > 0:
        rejected_by_reason["rank_cutoff"] += dropped_by_rank

    shortlisted_user_ids: set[str] = set()
    for entry in shortlisted:
        row, created = _upsert_discovery_candidate(
            session,
            workspace_id=workspace_id,
            account_user_id=str(entry.get("account_user_id") or ""),
            account_username=entry.get("account_username"),
            source_query=query,
            signal_post_count=int(entry.get("signal_post_count") or 0),
            followers_count=entry.get("followers_count"),
            tweet_count=entry.get("tweet_count"),
            avg_engagement=float(entry.get("avg_engagement") or 0.0),
            cadence_per_day=float(entry.get("cadence_per_day") or 0.0),
            score=int(entry.get("score") or 0),
            rationale=dict(entry.get("rationale") or {}),
            status="pending",
        )
        if row.status != "pending":
            continue
        shortlisted_user_ids.add(row.account_user_id)
        if created:
            discovered += 1
        else:
            updated += 1
        selected_candidates.append(
            {
                "candidate_id": row.id,
                "account_user_id": row.account_user_id,
                "account_username": row.account_username,
                "profile_url": build_x_profile_url(
                    account_user_id=row.account_user_id,
                    account_username=row.account_username,
                ),
                "score": row.score,
                "followers_count": row.followers_count,
                "signal_post_count": row.signal_post_count,
                "avg_engagement": float(row.avg_engagement or 0.0),
                "cadence_per_day": float(row.cadence_per_day or 0.0),
                "engagement_rate_pct": float(entry.get("engagement_rate_pct") or 0.0),
                "selection_reason": str((entry.get("rationale") or {}).get("selection_reason") or ""),
            }
        )

    pending_rows = list(
        session.scalars(
            select(XStrategyDiscoveryCandidate).where(
                XStrategyDiscoveryCandidate.workspace_id == workspace_id,
                XStrategyDiscoveryCandidate.status == "pending",
            )
        ).all()
    )
    now = datetime.now(timezone.utc)
    for row in pending_rows:
        if row.account_user_id in shortlisted_user_ids:
            continue
        row.status = "rejected_auto"
        row.updated_at = now
        pruned_pending += 1

    ranked_ids = [entry["candidate_id"] for entry in selected_candidates]
    session.flush()
    pending_count = len(list_pending_strategy_candidates(session, workspace_id=workspace_id, limit=200))

    session.add(
        WorkspaceEvent(
            workspace_id=workspace_id,
            event_type="x_strategy_discovery_completed",
            payload_json=_json_dumps(
                {
                    "status": "discovered",
                    "query": query,
                    "scanned_users": scanned_users,
                    "discovered": discovered,
                    "updated": updated,
                    "quality_rejected": quality_rejected,
                    "pruned_pending": pruned_pending,
                    "rejected_by_reason": dict(rejected_by_reason),
                    "pending_count": pending_count,
                    "criteria": criteria,
                    "candidate_ids": ranked_ids,
                    "errors": errors,
                }
            ),
        )
    )
    session.commit()

    return {
        "workspace_id": workspace_id,
        "status": "discovered",
        "query": query,
        "scanned_users": scanned_users,
        "discovered": discovered,
        "updated": updated,
        "quality_rejected": quality_rejected,
        "pruned_pending": pruned_pending,
        "rejected_by_reason": dict(rejected_by_reason),
        "criteria": criteria,
        "pending_count": pending_count,
        "candidates": selected_candidates,
        "errors": errors,
    }


def approve_strategy_candidate(
    session: Session,
    *,
    workspace_id: str,
    candidate_id: str,
    reviewed_by_user_id: str,
) -> Optional[Dict[str, Any]]:
    row = session.scalar(
        select(XStrategyDiscoveryCandidate).where(
            XStrategyDiscoveryCandidate.workspace_id == workspace_id,
            XStrategyDiscoveryCandidate.id == candidate_id,
        )
    )
    if row is None:
        return None

    watchlist_row = upsert_watchlist_account(
        session,
        workspace_id=workspace_id,
        account_user_id=row.account_user_id,
        account_username=row.account_username,
        added_by_user_id=reviewed_by_user_id,
    )
    row.status = "approved"
    row.reviewed_by_user_id = reviewed_by_user_id
    row.reviewed_at = datetime.now(timezone.utc)
    row.updated_at = datetime.now(timezone.utc)
    session.add(
        WorkspaceEvent(
            workspace_id=workspace_id,
            event_type="x_strategy_candidate_approved",
            payload_json=_json_dumps(
                {
                    "candidate_id": row.id,
                    "account_user_id": row.account_user_id,
                    "account_username": row.account_username,
                    "watchlist_id": watchlist_row.id,
                }
            ),
        )
    )
    session.commit()
    return {
        "candidate_id": row.id,
        "account_user_id": row.account_user_id,
        "account_username": row.account_username,
        "status": row.status,
        "watchlist_status": watchlist_row.status,
    }


def reject_strategy_candidate(
    session: Session,
    *,
    workspace_id: str,
    candidate_id: str,
    reviewed_by_user_id: str,
) -> Optional[Dict[str, Any]]:
    row = session.scalar(
        select(XStrategyDiscoveryCandidate).where(
            XStrategyDiscoveryCandidate.workspace_id == workspace_id,
            XStrategyDiscoveryCandidate.id == candidate_id,
        )
    )
    if row is None:
        return None

    row.status = "rejected"
    row.reviewed_by_user_id = reviewed_by_user_id
    row.reviewed_at = datetime.now(timezone.utc)
    row.updated_at = datetime.now(timezone.utc)
    session.add(
        WorkspaceEvent(
            workspace_id=workspace_id,
            event_type="x_strategy_candidate_rejected",
            payload_json=_json_dumps(
                {
                    "candidate_id": row.id,
                    "account_user_id": row.account_user_id,
                    "account_username": row.account_username,
                }
            ),
        )
    )
    session.commit()
    return {
        "candidate_id": row.id,
        "account_user_id": row.account_user_id,
        "account_username": row.account_username,
        "status": row.status,
    }


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
