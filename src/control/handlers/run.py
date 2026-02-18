"""Manual pipeline trigger handler."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List

from sqlalchemy import asc, select

from src.control.command_schema import ControlResponse
from src.control.services import (
    create_pipeline_run,
    create_queue_item,
    finish_pipeline_run,
    get_pipeline_run_by_idempotency,
    mark_queue_item_failed,
    mark_queue_item_published,
    parse_queue_metadata,
)
from src.control.state import acquire_pipeline_run_lock
from src.core.config import get_settings
from src.daily_post.service import generate_daily_post
from src.domain.agents.pipeline import evaluate_candidate_bundle
from src.ingestion.open_calls import list_candidates, run_open_calls_ingestion
from src.publishing.service import publish_post, publish_reply
from src.storage.models import ApprovalQueueItem

if TYPE_CHECKING:
    from src.control.command_router import CommandContext


_SUPPORTED_PIPELINES = {
    "ingest_open_calls",
    "propose_replies",
    "execute_approved",
    "daily_post",
}


def _parse_run_request(context: "CommandContext") -> tuple[str | None, bool]:
    if not context.command.args:
        return None, False
    pipeline = context.command.args[0].strip().lower()
    dry_run = False
    for token in context.command.args[1:]:
        normalized = token.strip().lower()
        if normalized in {"--dry-run", "dry_run=true", "dryrun=true"}:
            dry_run = True
    return pipeline, dry_run


def _run_ingest_open_calls(context: "CommandContext", *, dry_run: bool) -> Dict[str, Any]:
    if dry_run:
        return {"status": "dry_run", "pipeline": "ingest_open_calls"}

    result = run_open_calls_ingestion(
        context.session,
        workspace_id=context.envelope.workspace_id,
        x_client=context.x_client,
        max_results=20,
    )
    return {
        "status": "ok",
        "fetched": result.fetched,
        "stored_new": result.stored_new,
        "stored_updated": result.stored_updated,
        "ranked": result.ranked,
        "top_opportunity_score": result.top_opportunity_score,
    }


def _run_propose_replies(context: "CommandContext", *, dry_run: bool) -> Dict[str, Any]:
    settings = get_settings()
    candidates = list_candidates(
        context.session,
        workspace_id=context.envelope.workspace_id,
        limit=settings.scheduler_candidate_evaluation_limit,
    )

    queued = 0
    blocked_brand = 0
    blocked_cringe = 0

    for candidate in candidates:
        bundle = evaluate_candidate_bundle(
            {
                "workspace_id": context.envelope.workspace_id,
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
        cringe_ok = not bool(bundle["cringe_guard"]["cringe"])
        if not brand_ok:
            blocked_brand += 1
        if not cringe_ok:
            blocked_cringe += 1
        if not (brand_ok and cringe_ok):
            continue

        reply_text = str(bundle["reply_draft"]["text"])
        queue_idempotency = f"candidate:{candidate.id}"
        if dry_run:
            queued += 1
            continue

        create_queue_item(
            context.session,
            workspace_id=context.envelope.workspace_id,
            item_type="reply",
            content_text=reply_text,
            source_kind="ingestion_candidate",
            source_ref_id=candidate.id,
            intent=candidate.intent,
            opportunity_score=candidate.opportunity_score,
            idempotency_key=queue_idempotency,
            metadata={
                "in_reply_to_tweet_id": candidate.source_tweet_id,
                "thread_id": candidate.conversation_id,
                "target_author_id": candidate.author_id,
                "candidate_url": candidate.url,
            },
        )
        queued += 1

    return {
        "status": "dry_run" if dry_run else "ok",
        "evaluated": len(candidates),
        "queued": queued,
        "blocked_brand": blocked_brand,
        "blocked_cringe": blocked_cringe,
    }


def _run_execute_approved(context: "CommandContext", *, dry_run: bool) -> Dict[str, Any]:
    workspace_id = context.envelope.workspace_id
    approved_items: List[ApprovalQueueItem] = list(
        context.session.scalars(
            select(ApprovalQueueItem)
            .where(
                ApprovalQueueItem.workspace_id == workspace_id,
                ApprovalQueueItem.status == "approved",
            )
            .order_by(asc(ApprovalQueueItem.created_at))
            .limit(20)
        ).all()
    )

    published = 0
    failed = 0

    for item in approved_items:
        if dry_run:
            published += 1
            continue

        metadata = parse_queue_metadata(item)
        if item.item_type == "reply":
            in_reply_to_tweet_id = str(metadata.get("in_reply_to_tweet_id") or "").strip()
            if not in_reply_to_tweet_id:
                mark_queue_item_failed(context.session, item=item, error_message="missing_reply_target")
                failed += 1
                continue
            result = publish_reply(
                context.session,
                workspace_id=workspace_id,
                text=item.content_text,
                in_reply_to_tweet_id=in_reply_to_tweet_id,
                thread_id=(str(metadata.get("thread_id")) if metadata.get("thread_id") else None),
                target_author_id=(str(metadata.get("target_author_id")) if metadata.get("target_author_id") else None),
                x_client=context.x_client,
            )
        else:
            result = publish_post(
                context.session,
                workspace_id=workspace_id,
                text=item.content_text,
                x_client=context.x_client,
            )

        if result.published:
            mark_queue_item_published(context.session, item=item, external_post_id=result.external_post_id)
            published += 1
        else:
            mark_queue_item_failed(context.session, item=item, error_message=result.message)
            failed += 1

    return {
        "status": "dry_run" if dry_run else "ok",
        "approved_items": len(approved_items),
        "published": published,
        "failed": failed,
    }


def _run_daily_post(context: "CommandContext", *, dry_run: bool) -> Dict[str, Any]:
    result = generate_daily_post(
        context.session,
        workspace_id=context.envelope.workspace_id,
        topic=None,
        auto_publish=False,
        x_client=context.x_client,
    )

    if dry_run:
        return {
            "status": "dry_run",
            "draft_id": result.draft_id,
            "draft_status": result.status,
            "seed_count": result.seed_count,
        }

    if result.status == "ready":
        create_queue_item(
            context.session,
            workspace_id=context.envelope.workspace_id,
            item_type="post",
            content_text=result.text,
            source_kind="daily_post_draft",
            source_ref_id=result.draft_id,
            intent="daily_post",
            opportunity_score=100,
            idempotency_key=f"daily_post:{result.draft_id}",
            metadata={"draft_id": result.draft_id},
        )

    return {
        "status": "ok",
        "draft_id": result.draft_id,
        "draft_status": result.status,
        "seed_count": result.seed_count,
        "queued": result.status == "ready",
    }


def _execute_pipeline(context: "CommandContext", *, pipeline: str, dry_run: bool) -> Dict[str, Any]:
    if pipeline == "ingest_open_calls":
        return _run_ingest_open_calls(context, dry_run=dry_run)
    if pipeline == "propose_replies":
        return _run_propose_replies(context, dry_run=dry_run)
    if pipeline == "execute_approved":
        return _run_execute_approved(context, dry_run=dry_run)
    if pipeline == "daily_post":
        return _run_daily_post(context, dry_run=dry_run)
    raise ValueError("unsupported_pipeline")


def handle(context: "CommandContext") -> ControlResponse:
    workspace_id = context.envelope.workspace_id
    pipeline, dry_run = _parse_run_request(context)
    if not pipeline:
        return ControlResponse(
            success=False,
            message="usage: /run <pipeline> [dry_run=true]",
            data={"supported": sorted(_SUPPORTED_PIPELINES)},
        )

    if pipeline not in _SUPPORTED_PIPELINES:
        return ControlResponse(
            success=False,
            message="unsupported_pipeline",
            data={"pipeline": pipeline, "supported": sorted(_SUPPORTED_PIPELINES)},
        )

    existing = get_pipeline_run_by_idempotency(
        context.session,
        workspace_id=workspace_id,
        pipeline_name=pipeline,
        idempotency_key=context.idempotency_key,
    )
    if existing is not None:
        return ControlResponse(
            success=True,
            message="run_idempotent",
            data={
                "pipeline": pipeline,
                "status": existing.status,
                "run_id": existing.id,
            },
        )

    settings = get_settings()
    lock = acquire_pipeline_run_lock(
        context.redis_client,
        workspace_id=workspace_id,
        pipeline=pipeline,
        ttl_seconds=settings.control_run_lock_ttl_seconds,
    )
    if lock is None:
        return ControlResponse(
            success=False,
            message="pipeline_already_running",
            data={"pipeline": pipeline},
        )

    run_row = create_pipeline_run(
        context.session,
        workspace_id=workspace_id,
        pipeline_name=pipeline,
        dry_run=dry_run,
        request_id=context.request_id,
        idempotency_key=context.idempotency_key,
        actor_user_id=context.actor.user_id,
        telegram_user_id=context.envelope.telegram_user_id,
    )

    try:
        result = _execute_pipeline(context, pipeline=pipeline, dry_run=dry_run)
        finish_pipeline_run(
            context.session,
            run=run_row,
            status="succeeded",
            result=result,
            error_message=None,
        )
        return ControlResponse(
            success=True,
            message="pipeline_executed",
            data={
                "pipeline": pipeline,
                "run_id": run_row.id,
                "dry_run": dry_run,
                "result": result,
            },
        )
    except Exception as exc:
        finish_pipeline_run(
            context.session,
            run=run_row,
            status="failed",
            result={"pipeline": pipeline},
            error_message=str(exc),
        )
        return ControlResponse(
            success=False,
            message="pipeline_failed",
            data={
                "pipeline": pipeline,
                "run_id": run_row.id,
                "error": str(exc),
            },
        )
    finally:
        lock.release()
