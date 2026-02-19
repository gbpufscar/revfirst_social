"""Manual pipeline trigger handler."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
from src.publishing.service import publish_blog, publish_email, publish_instagram, publish_post, publish_reply
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


def _parse_scheduled_for(metadata: Dict[str, Any]) -> datetime | None:
    raw_value = metadata.get("scheduled_for")
    if raw_value is None:
        return None
    value = str(raw_value).strip()
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


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
    scheduled_pending = 0

    for item in approved_items:
        metadata = parse_queue_metadata(item)
        if item.item_type == "instagram":
            scheduled_for = _parse_scheduled_for(metadata)
            if scheduled_for is not None and scheduled_for > datetime.now(timezone.utc):
                scheduled_pending += 1
                continue

        if dry_run:
            published += 1
            continue

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
        elif item.item_type == "post":
            result = publish_post(
                context.session,
                workspace_id=workspace_id,
                text=item.content_text,
                x_client=context.x_client,
            )
        elif item.item_type == "email":
            recipients_raw = metadata.get("recipients")
            recipients = []
            if isinstance(recipients_raw, str):
                recipients = [value.strip() for value in recipients_raw.split(",") if value.strip()]
            elif isinstance(recipients_raw, list):
                recipients = [str(value).strip() for value in recipients_raw if str(value).strip()]

            result = publish_email(
                context.session,
                workspace_id=workspace_id,
                subject=str(metadata.get("subject") or "RevFirst update"),
                body=item.content_text,
                recipients=recipients,
                source_kind=item.source_kind,
                source_ref_id=item.source_ref_id,
            )
        elif item.item_type == "blog":
            result = publish_blog(
                context.session,
                workspace_id=workspace_id,
                title=str(metadata.get("title") or "RevFirst blog draft"),
                markdown=item.content_text,
                source_kind=item.source_kind,
                source_ref_id=item.source_ref_id,
            )
        elif item.item_type == "instagram":
            scheduled_for = _parse_scheduled_for(metadata)
            image_url = str(
                metadata.get("image_url") or metadata.get("media_url") or metadata.get("asset_url") or ""
            ).strip()
            result = publish_instagram(
                context.session,
                workspace_id=workspace_id,
                caption=item.content_text,
                image_url=(image_url or None),
                source_kind=item.source_kind,
                source_ref_id=item.source_ref_id,
                scheduled_for=(scheduled_for.isoformat() if scheduled_for is not None else None),
            )
        else:
            mark_queue_item_failed(context.session, item=item, error_message="unsupported_queue_item_type")
            failed += 1
            continue

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
        "scheduled_pending": scheduled_pending,
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

    queued_types: list[str] = []
    if result.status == "ready":
        channel_targets = list(result.channel_targets)
        previews = dict(result.channel_previews)
        if "x" in channel_targets:
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
            queued_types.append("post")

        if "email" in channel_targets:
            email_preview = previews.get("email") or {}
            email_subject = str(email_preview.get("title") or "RevFirst update")
            email_body = str(email_preview.get("body") or result.text)
            create_queue_item(
                context.session,
                workspace_id=context.envelope.workspace_id,
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
            create_queue_item(
                context.session,
                workspace_id=context.envelope.workspace_id,
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
                },
            )
            queued_types.append("blog")

        if "instagram" in channel_targets:
            settings = get_settings()
            instagram_preview = previews.get("instagram") or {}
            instagram_caption = str(instagram_preview.get("body") or result.text)
            instagram_metadata = instagram_preview.get("metadata")
            preview_metadata = instagram_metadata if isinstance(instagram_metadata, dict) else {}
            queue_metadata: Dict[str, Any] = {
                "draft_id": result.draft_id,
                "image_url": str(preview_metadata.get("image_url") or "").strip(),
            }
            if settings.instagram_default_schedule_hours_ahead > 0:
                scheduled_for = datetime.now(timezone.utc) + timedelta(
                    hours=settings.instagram_default_schedule_hours_ahead
                )
                queue_metadata["scheduled_for"] = scheduled_for.isoformat()

            create_queue_item(
                context.session,
                workspace_id=context.envelope.workspace_id,
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
        "status": "ok",
        "draft_id": result.draft_id,
        "draft_status": result.status,
        "seed_count": result.seed_count,
        "queued": len(queued_types),
        "queued_types": queued_types,
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
