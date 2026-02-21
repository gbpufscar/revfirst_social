"""Manual pipeline trigger handler."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Dict

from src.control.command_schema import ControlResponse
from src.control.queue_executor import execute_approved_queue_items
from src.control.services import (
    create_pipeline_run,
    create_queue_item,
    finish_pipeline_run,
    get_pipeline_run_by_idempotency,
)
from src.control.state import acquire_pipeline_run_lock
from src.core.config import get_settings
from src.daily_post.service import generate_daily_post
from src.domain.agents.pipeline import evaluate_candidate_bundle
from src.ingestion.open_calls import list_candidates, run_open_calls_ingestion
from src.media.service import generate_image_asset

if TYPE_CHECKING:
    from src.control.command_router import CommandContext


_SUPPORTED_PIPELINES = {
    "ingest_open_calls",
    "propose_replies",
    "execute_approved",
    "daily_post",
}


def _parse_run_request(context: "CommandContext") -> tuple[str | None, bool, bool]:
    if not context.command.args:
        return None, False, False
    pipeline = context.command.args[0].strip().lower()
    dry_run = False
    owner_override = False
    for token in context.command.args[1:]:
        normalized = token.strip().lower()
        if normalized in {"--dry-run", "dry_run=true", "dryrun=true"}:
            dry_run = True
        if normalized in {"override", "--override", "owner_override=true"} and context.actor.role == "owner":
            owner_override = True
    return pipeline, dry_run, owner_override


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


def _run_execute_approved(context: "CommandContext", *, dry_run: bool, owner_override: bool) -> Dict[str, Any]:
    return execute_approved_queue_items(
        context.session,
        workspace_id=context.envelope.workspace_id,
        x_client=context.x_client,
        dry_run=dry_run,
        owner_override=owner_override,
        due_only=True,
        limit=20,
    )


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
                context.session,
                workspace_id=context.envelope.workspace_id,
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
            blog_image_info = generated_images.get("blog") or {}
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
                    "image_url": blog_image_info.get("public_url"),
                    "media_asset_id": blog_image_info.get("asset_id"),
                },
            )
            queued_types.append("blog")

        if "instagram" in channel_targets:
            settings = get_settings()
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
                return {
                    "status": "ok",
                    "draft_id": result.draft_id,
                    "draft_status": result.status,
                    "seed_count": result.seed_count,
                    "queued": len(queued_types),
                    "queued_types": queued_types,
                    "blocked_channels": blocked_channels,
                    "generated_images": generated_images,
                }
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
        "blocked_channels": blocked_channels,
        "generated_images": generated_images,
    }


def _execute_pipeline(
    context: "CommandContext",
    *,
    pipeline: str,
    dry_run: bool,
    owner_override: bool,
) -> Dict[str, Any]:
    if pipeline == "ingest_open_calls":
        return _run_ingest_open_calls(context, dry_run=dry_run)
    if pipeline == "propose_replies":
        return _run_propose_replies(context, dry_run=dry_run)
    if pipeline == "execute_approved":
        return _run_execute_approved(context, dry_run=dry_run, owner_override=owner_override)
    if pipeline == "daily_post":
        return _run_daily_post(context, dry_run=dry_run)
    raise ValueError("unsupported_pipeline")


def handle(context: "CommandContext") -> ControlResponse:
    workspace_id = context.envelope.workspace_id
    pipeline, dry_run, owner_override = _parse_run_request(context)
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
        result = _execute_pipeline(
            context,
            pipeline=pipeline,
            dry_run=dry_run,
            owner_override=owner_override,
        )
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
