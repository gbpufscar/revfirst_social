"""Workspace-scoped orchestration pipeline used by the scheduler."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any, Dict

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from src.analytics.x_performance_agent import build_workspace_growth_report, collect_workspace_growth_snapshot
from src.control.queue_executor import execute_approved_queue_items
from src.control.services import create_queue_item as create_control_queue_item
from src.core.config import get_settings
from src.core.logger import get_logger
from src.core.metrics import record_replies_generated, record_reply_blocked
from src.daily_post.service import generate_daily_post
from src.domain.agents.pipeline import evaluate_candidate_bundle
from src.ingestion.open_calls import list_candidates, run_open_calls_ingestion
from src.integrations.x.service import get_workspace_x_access_token
from src.integrations.x.x_client import XClient
from src.media.service import generate_image_asset
from src.operations.daily_operational_reporter import run_daily_operational_report
from src.operations.stability_guard_agent import run_workspace_stability_guard_cycle
from src.storage.redis_client import get_client as get_redis_client
from src.storage.models import DailyPostDraft, WorkspaceEvent
from src.strategy.x_growth_strategy_agent import run_workspace_strategy_discovery, run_workspace_strategy_scan


_ALLOWED_QUEUE_TYPES = {"reply", "post", "email", "blog", "instagram"}
_DAILY_OPERATIONAL_REPORT_EVENT = "daily_operational_report_sent"

logger = get_logger("revfirst.orchestrator.pipeline")


def _json_dumps(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True)


def _create_queue_item(
    session: Session,
    *,
    workspace_id: str,
    item_type: str,
    content_text: str,
    source_kind: str | None,
    source_ref_id: str | None,
    intent: str | None,
    opportunity_score: int | None,
    metadata: Dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> None:
    normalized_type = item_type.strip().lower()
    if normalized_type not in _ALLOWED_QUEUE_TYPES:
        raise ValueError("unsupported_queue_item_type")
    create_control_queue_item(
        session,
        workspace_id=workspace_id,
        item_type=normalized_type,
        content_text=content_text,
        source_kind=source_kind,
        source_ref_id=source_ref_id,
        intent=intent,
        opportunity_score=opportunity_score,
        metadata=metadata,
        idempotency_key=idempotency_key,
    )


def _is_brand_ok(bundle: Dict[str, Any]) -> bool:
    payload = bundle.get("brand_consistency")
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("passed"))


def _is_cringe_ok(bundle: Dict[str, Any]) -> bool:
    payload = bundle.get("cringe_guard")
    if not isinstance(payload, dict):
        return False
    if "passed" in payload:
        return bool(payload.get("passed"))
    return not bool(payload.get("cringe"))


def _has_recent_daily_post_draft(
    session: Session,
    *,
    workspace_id: str,
    interval_hours: int,
) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, interval_hours))
    recent_draft_id = session.scalar(
        select(DailyPostDraft.id).where(
            DailyPostDraft.workspace_id == workspace_id,
            DailyPostDraft.created_at >= cutoff,
        )
    )
    return recent_draft_id is not None


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_daily_operational_report_due(
    session: Session,
    *,
    workspace_id: str,
    now: datetime,
) -> bool:
    now_utc = _normalize_datetime(now)
    if now_utc.hour == 0 and now_utc.minute < 10:
        return False

    last_sent_at = session.scalar(
        select(WorkspaceEvent.created_at)
        .where(
            WorkspaceEvent.workspace_id == workspace_id,
            WorkspaceEvent.event_type == _DAILY_OPERATIONAL_REPORT_EVENT,
        )
        .order_by(desc(WorkspaceEvent.created_at))
        .limit(1)
    )
    if last_sent_at is None:
        return True
    return _normalize_datetime(last_sent_at).date() < now_utc.date()


def _run_daily_operational_reporter(
    session: Session,
    *,
    workspace_id: str,
) -> Dict[str, Any]:
    now_utc = _utc_now()
    if not _is_daily_operational_report_due(
        session,
        workspace_id=workspace_id,
        now=now_utc,
    ):
        return {
            "status": "skipped_not_due",
            "date_utc": now_utc.date().isoformat(),
            "scheduled_at_utc": "00:10",
        }

    try:
        report_result = run_daily_operational_report(
            session,
            workspace_id=workspace_id,
            redis_client=get_redis_client(),
            now=now_utc,
        )
    except Exception as exc:  # pragma: no cover
        try:
            session.rollback()
        except Exception:
            pass
        logger.warning(
            "daily_operational_reporter_unhandled_error",
            workspace_id=workspace_id,
            error=str(exc),
        )
        return {
            "status": "failed",
            "error": str(exc),
            "date_utc": now_utc.date().isoformat(),
            "scheduled_at_utc": "00:10",
        }

    snapshot = report_result.get("snapshot") if isinstance(report_result.get("snapshot"), dict) else {}
    delivery = report_result.get("delivery") if isinstance(report_result.get("delivery"), dict) else {}
    event_payload = {
        "status": str(report_result.get("status") or "error"),
        "date_utc": now_utc.date().isoformat(),
        "scheduled_at_utc": "00:10",
        "risk_assessment": str(snapshot.get("risk_assessment") or "LOW"),
        "delivery": {
            "attempted": int(delivery.get("attempted") or 0),
            "delivered": int(delivery.get("delivered") or 0),
            "failed": int(delivery.get("failed") or 0),
        },
    }
    session.add(
        WorkspaceEvent(
            workspace_id=workspace_id,
            event_type=_DAILY_OPERATIONAL_REPORT_EVENT,
            payload_json=_json_dumps(event_payload),
            created_at=now_utc,
        )
    )
    session.commit()
    return {
        "status": "executed",
        "report_status": event_payload["status"],
        "date_utc": event_payload["date_utc"],
        "scheduled_at_utc": "00:10",
        "risk_assessment": event_payload["risk_assessment"],
        "delivery": event_payload["delivery"],
    }


def _is_workspace_event_due(
    session: Session,
    *,
    workspace_id: str,
    event_type: str,
    interval_hours: int,
) -> bool:
    safe_interval = max(1, interval_hours)
    last_event_at = session.scalar(
        select(WorkspaceEvent.created_at)
        .where(
            WorkspaceEvent.workspace_id == workspace_id,
            WorkspaceEvent.event_type == event_type,
        )
        .order_by(WorkspaceEvent.created_at.desc())
        .limit(1)
    )
    if last_event_at is None:
        return True

    cutoff = datetime.now(timezone.utc) - timedelta(hours=safe_interval)
    return _normalize_datetime(last_event_at) <= cutoff


def _run_growth_agent(
    session: Session,
    *,
    workspace_id: str,
    x_client: XClient,
) -> Dict[str, Any]:
    settings = get_settings()
    interval_hours = max(1, settings.scheduler_growth_collection_interval_hours)
    if not _is_workspace_event_due(
        session,
        workspace_id=workspace_id,
        event_type="x_growth_snapshot_collected",
        interval_hours=interval_hours,
    ):
        return {
            "status": "skipped_interval",
            "interval_hours": interval_hours,
        }

    snapshot = collect_workspace_growth_snapshot(
        session,
        workspace_id=workspace_id,
        x_client=x_client,
    )
    report = build_workspace_growth_report(
        session,
        workspace_id=workspace_id,
        period_days=1,
        persist_insight=True,
    )
    return {
        "status": "executed",
        "interval_hours": interval_hours,
        "snapshot_id": snapshot.get("snapshot_id"),
        "post_snapshots": snapshot.get("post_snapshots"),
        "errors": snapshot.get("errors", []),
        "kpis": report.get("kpis", {}),
        "recommendations": report.get("recommendations", []),
    }


def _run_strategy_agent(
    session: Session,
    *,
    workspace_id: str,
    x_client: XClient,
) -> Dict[str, Any]:
    settings = get_settings()
    interval_hours = max(1, settings.scheduler_strategy_scan_interval_hours)
    if not _is_workspace_event_due(
        session,
        workspace_id=workspace_id,
        event_type="x_strategy_scan_completed",
        interval_hours=interval_hours,
    ):
        return {
            "status": "skipped_interval",
            "interval_hours": interval_hours,
        }

    result = run_workspace_strategy_scan(
        session,
        workspace_id=workspace_id,
        x_client=x_client,
    )
    if not isinstance(result, dict):
        return {
            "status": "failed",
            "error": "invalid_strategy_scan_response",
            "interval_hours": interval_hours,
        }
    result["interval_hours"] = interval_hours
    return result


def _run_strategy_discovery_agent(
    session: Session,
    *,
    workspace_id: str,
    x_client: XClient,
) -> Dict[str, Any]:
    settings = get_settings()
    interval_hours = max(1, settings.scheduler_strategy_discovery_interval_hours)
    if not _is_workspace_event_due(
        session,
        workspace_id=workspace_id,
        event_type="x_strategy_discovery_completed",
        interval_hours=interval_hours,
    ):
        return {
            "status": "skipped_interval",
            "interval_hours": interval_hours,
        }

    result = run_workspace_strategy_discovery(
        session,
        workspace_id=workspace_id,
        x_client=x_client,
    )
    if not isinstance(result, dict):
        return {
            "status": "failed",
            "error": "invalid_strategy_discovery_response",
            "interval_hours": interval_hours,
        }
    result["interval_hours"] = interval_hours
    return result


def _queue_daily_post(
    session: Session,
    *,
    workspace_id: str,
    x_client: XClient,
) -> Dict[str, Any]:
    settings = get_settings()
    interval_hours = max(1, settings.scheduler_daily_post_interval_hours)
    if _has_recent_daily_post_draft(
        session,
        workspace_id=workspace_id,
        interval_hours=interval_hours,
    ):
        return {
            "status": "skipped_recent_draft",
            "interval_hours": interval_hours,
            "queued": 0,
            "queued_types": [],
            "blocked_channels": {},
            "generated_images": {},
        }

    result = generate_daily_post(
        session,
        workspace_id=workspace_id,
        topic=None,
        auto_publish=False,
        x_client=x_client,
    )

    queued_types: list[str] = []
    blocked_channels: Dict[str, str] = {}
    generated_images: Dict[str, Dict[str, Any]] = {}
    if result.status == "ready":
        channel_targets = list(result.channel_targets)
        previews = dict(result.channel_previews)

        for channel in ["x", "blog", "instagram"]:
            if channel not in channel_targets:
                continue
            preview = previews.get(channel) or {}
            preview_metadata = preview.get("metadata")
            metadata = preview_metadata if isinstance(preview_metadata, dict) else {}
            existing_image_url = str(metadata.get("image_url") or "").strip()
            if existing_image_url:
                generated_images[channel] = {
                    "status": "existing",
                    "public_url": existing_image_url,
                    "asset_id": metadata.get("media_asset_id"),
                }
                continue

            media_result = generate_image_asset(
                session,
                workspace_id=workspace_id,
                channel=channel,
                content_text=result.text,
                source_kind="daily_post_draft",
                source_ref_id=result.draft_id,
                idempotency_key=f"daily_post_media:{channel}:{result.draft_id}",
                metadata={"draft_id": result.draft_id, "content_type": "short_post"},
            )
            generated_images[channel] = {
                "status": media_result.status,
                "public_url": media_result.public_url,
                "asset_id": media_result.asset_id,
                "message": media_result.message,
            }

        if "x" in channel_targets:
            x_image_info = generated_images.get("x") or {}
            _create_queue_item(
                session,
                workspace_id=workspace_id,
                item_type="post",
                content_text=result.text,
                source_kind="daily_post_draft",
                source_ref_id=result.draft_id,
                intent="daily_post",
                opportunity_score=100,
                idempotency_key=f"daily_post:{result.draft_id}",
                metadata={
                    "draft_id": result.draft_id,
                    "image_url": x_image_info.get("public_url"),
                    "media_asset_id": x_image_info.get("asset_id"),
                },
            )
            queued_types.append("post")

        if "email" in channel_targets:
            email_preview = previews.get("email") or {}
            email_subject = str(email_preview.get("title") or "RevFirst update")
            email_body = str(email_preview.get("body") or result.text)
            _create_queue_item(
                session,
                workspace_id=workspace_id,
                item_type="email",
                content_text=email_body,
                source_kind="daily_post_draft",
                source_ref_id=result.draft_id,
                intent="daily_post",
                opportunity_score=100,
                idempotency_key=f"daily_post_email:{result.draft_id}",
                metadata={
                    "draft_id": result.draft_id,
                    "subject": email_subject,
                },
            )
            queued_types.append("email")

        if "blog" in channel_targets:
            blog_preview = previews.get("blog") or {}
            blog_title = str(blog_preview.get("title") or "RevFirst blog draft")
            blog_body = str(blog_preview.get("body") or result.text)
            blog_image_info = generated_images.get("blog") or {}
            _create_queue_item(
                session,
                workspace_id=workspace_id,
                item_type="blog",
                content_text=blog_body,
                source_kind="daily_post_draft",
                source_ref_id=result.draft_id,
                intent="daily_post",
                opportunity_score=100,
                idempotency_key=f"daily_post_blog:{result.draft_id}",
                metadata={
                    "draft_id": result.draft_id,
                    "title": blog_title,
                    "image_url": blog_image_info.get("public_url"),
                    "media_asset_id": blog_image_info.get("asset_id"),
                },
            )
            queued_types.append("blog")

        if "instagram" in channel_targets:
            instagram_preview = previews.get("instagram") or {}
            instagram_caption = str(instagram_preview.get("body") or result.text)
            instagram_metadata = instagram_preview.get("metadata")
            preview_metadata = instagram_metadata if isinstance(instagram_metadata, dict) else {}
            instagram_image_info = generated_images.get("instagram") or {}
            resolved_image_url = str(
                preview_metadata.get("image_url") or instagram_image_info.get("public_url") or ""
            ).strip()
            if not resolved_image_url:
                blocked_channels["instagram"] = "image_unavailable"
            else:
                queue_metadata: Dict[str, Any] = {
                    "draft_id": result.draft_id,
                    "image_url": resolved_image_url,
                    "media_asset_id": instagram_image_info.get("asset_id"),
                }
                if settings.instagram_default_schedule_hours_ahead > 0:
                    scheduled_for = datetime.now(timezone.utc) + timedelta(
                        hours=settings.instagram_default_schedule_hours_ahead
                    )
                    queue_metadata["scheduled_for"] = scheduled_for.isoformat()

                _create_queue_item(
                    session,
                    workspace_id=workspace_id,
                    item_type="instagram",
                    content_text=instagram_caption,
                    source_kind="daily_post_draft",
                    source_ref_id=result.draft_id,
                    intent="daily_post",
                    opportunity_score=100,
                    idempotency_key=f"daily_post_instagram:{result.draft_id}",
                    metadata=queue_metadata,
                )
                queued_types.append("instagram")

    return {
        "status": result.status,
        "draft_id": result.draft_id,
        "seed_count": result.seed_count,
        "queued": len(queued_types),
        "queued_types": queued_types,
        "blocked_channels": blocked_channels,
        "generated_images": generated_images,
    }


def execute_due_scheduled_posts(
    session: Session,
    *,
    workspace_id: str,
    x_client: XClient,
) -> Dict[str, Any]:
    return execute_approved_queue_items(
        session,
        workspace_id=workspace_id,
        x_client=x_client,
        dry_run=False,
        owner_override=False,
        due_only=True,
        limit=20,
    )


def run_workspace_pipeline(
    session: Session,
    *,
    workspace_id: str,
    x_client: XClient,
) -> Dict[str, Any]:
    """Run one workspace pipeline iteration without cross-tenant state."""

    settings = get_settings()
    stability_guard: Dict[str, Any] = {"status": "disabled"}
    if settings.stability_guard_scheduler_checks_enabled:
        try:
            stability_guard = run_workspace_stability_guard_cycle(
                session,
                workspace_id=workspace_id,
                redis_client=get_redis_client(),
                actor_user_id=None,
                trigger="scheduler",
            )
        except Exception as exc:
            stability_guard = {"status": "failed", "error": str(exc)}

    try:
        daily_operational_report = _run_daily_operational_reporter(
            session,
            workspace_id=workspace_id,
        )
    except Exception as exc:  # pragma: no cover
        logger.warning(
            "daily_operational_reporter_pipeline_wrapper_failed",
            workspace_id=workspace_id,
            error=str(exc),
        )
        daily_operational_report = {"status": "failed", "error": str(exc)}

    containment = stability_guard.get("containment") if isinstance(stability_guard.get("containment"), dict) else {}
    kill_switch_action = (
        stability_guard.get("kill_switch_action") if isinstance(stability_guard.get("kill_switch_action"), dict) else {}
    )
    if containment.get("actions_applied") or kill_switch_action.get("applied"):
        return {
            "status": "skipped",
            "reason": "stability_containment_applied",
            "ingested": 0,
            "evaluated_candidates": 0,
            "eligible_reply_candidates": 0,
            "stability_guard": stability_guard,
            "daily_operational_report": daily_operational_report,
        }

    access_token = get_workspace_x_access_token(session, workspace_id=workspace_id)
    if access_token is None:
        return {
            "status": "skipped",
            "reason": "x_oauth_missing",
            "ingested": 0,
            "evaluated_candidates": 0,
            "eligible_reply_candidates": 0,
            "stability_guard": stability_guard,
            "daily_operational_report": daily_operational_report,
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
    queued_reply_candidates = 0
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
        brand_ok = _is_brand_ok(bundle)
        cringe_ok = _is_cringe_ok(bundle)
        if brand_ok and cringe_ok:
            eligible += 1
            if settings.scheduler_auto_queue_replies_enabled:
                reply_payload = bundle.get("reply_draft")
                reply_text = ""
                if isinstance(reply_payload, dict):
                    reply_text = str(reply_payload.get("text") or "").strip()
                if reply_text:
                    _create_queue_item(
                        session,
                        workspace_id=workspace_id,
                        item_type="reply",
                        content_text=reply_text,
                        source_kind="ingestion_candidate",
                        source_ref_id=candidate.id,
                        intent=candidate.intent,
                        opportunity_score=candidate.opportunity_score,
                        idempotency_key=f"candidate:{candidate.id}",
                        metadata={
                            "in_reply_to_tweet_id": candidate.source_tweet_id,
                            "thread_id": candidate.conversation_id,
                            "target_author_id": candidate.author_id,
                            "candidate_url": candidate.url,
                        },
                    )
                    queued_reply_candidates += 1
        else:
            if not brand_ok:
                record_reply_blocked(workspace_id=workspace_id, reason="brand_guard")
            if not cringe_ok:
                record_reply_blocked(workspace_id=workspace_id, reason="cringe_guard")

    daily_post_queue = {
        "status": "disabled",
        "queued": 0,
        "queued_types": [],
        "blocked_channels": {},
        "generated_images": {},
    }
    if settings.scheduler_auto_queue_daily_post_enabled:
        try:
            daily_post_queue = _queue_daily_post(
                session,
                workspace_id=workspace_id,
                x_client=x_client,
            )
        except Exception as exc:
            daily_post_queue = {
                "status": "failed",
                "error": str(exc),
                "queued": 0,
                "queued_types": [],
                "blocked_channels": {},
                "generated_images": {},
            }

    scheduled_publish_execution = {"status": "disabled"}
    try:
        scheduled_publish_execution = execute_due_scheduled_posts(
            session,
            workspace_id=workspace_id,
            x_client=x_client,
        )
    except Exception as exc:
        scheduled_publish_execution = {
            "status": "failed",
            "error": str(exc),
        }

    growth_agent: Dict[str, Any] = {"status": "disabled"}
    if settings.scheduler_growth_collection_enabled:
        try:
            growth_agent = _run_growth_agent(
                session,
                workspace_id=workspace_id,
                x_client=x_client,
            )
        except Exception as exc:
            growth_agent = {
                "status": "failed",
                "error": str(exc),
            }

    strategy_agent: Dict[str, Any] = {"status": "disabled"}
    if settings.scheduler_strategy_scan_enabled:
        try:
            strategy_agent = _run_strategy_agent(
                session,
                workspace_id=workspace_id,
                x_client=x_client,
            )
        except Exception as exc:
            strategy_agent = {
                "status": "failed",
                "error": str(exc),
            }

    strategy_discovery_agent: Dict[str, Any] = {"status": "disabled"}
    if settings.scheduler_strategy_discovery_enabled:
        try:
            strategy_discovery_agent = _run_strategy_discovery_agent(
                session,
                workspace_id=workspace_id,
                x_client=x_client,
            )
        except Exception as exc:
            strategy_discovery_agent = {
                "status": "failed",
                "error": str(exc),
            }

    return {
        "status": "executed",
        "ingested": ingestion.fetched,
        "stored_new": ingestion.stored_new,
        "stored_updated": ingestion.stored_updated,
        "ranked": ingestion.ranked,
        "top_opportunity_score": ingestion.top_opportunity_score,
        "evaluated_candidates": len(candidates),
        "eligible_reply_candidates": eligible,
        "queued_reply_candidates": queued_reply_candidates,
        "daily_post_queue": daily_post_queue,
        "scheduled_publish_execution": scheduled_publish_execution,
        "growth_agent": growth_agent,
        "strategy_discovery_agent": strategy_discovery_agent,
        "strategy_agent": strategy_agent,
        "stability_guard": stability_guard,
        "daily_operational_report": daily_operational_report,
    }
