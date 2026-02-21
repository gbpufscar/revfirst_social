"""Persistence helpers for control-plane tables."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from src.storage.models import (
    AdminAction,
    ApprovalQueueItem,
    PipelineRun,
    WorkspaceControlSetting,
)
from src.control.state import get_workspace_mode_cached, set_workspace_mode_cached

_DEFAULT_CHANNELS = {
    "x": True,
    "email": False,
    "blog": False,
    "instagram": False,
}
DEFAULT_OPERATIONAL_MODE = "semi_autonomous"
VALID_OPERATIONAL_MODES = {
    "manual",
    "semi_autonomous",
    "autonomous_limited",
    "containment",
}


_ALLOWED_CHANNELS = set(_DEFAULT_CHANNELS.keys())
_ALLOWED_QUEUE_TYPES = {"reply", "post", "email", "blog", "instagram"}


def normalize_operational_mode(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in VALID_OPERATIONAL_MODES:
        return normalized
    return DEFAULT_OPERATIONAL_MODE


def scheduler_enabled_for_mode(mode: str) -> bool:
    normalized = normalize_operational_mode(mode)
    return normalized in {"semi_autonomous", "autonomous_limited"}


def publishing_allowed_for_mode(mode: str, *, owner_override: bool = False) -> bool:
    normalized = normalize_operational_mode(mode)
    if normalized == "containment":
        return bool(owner_override)
    return True


def _json_dumps(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True)


def _json_load_dict(payload: str) -> Dict[str, Any]:
    try:
        loaded = json.loads(payload)
    except Exception:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return loaded


def get_or_create_control_setting(session: Session, *, workspace_id: str) -> WorkspaceControlSetting:
    setting = session.scalar(
        select(WorkspaceControlSetting).where(
            WorkspaceControlSetting.workspace_id == workspace_id,
        )
    )
    if setting is not None:
        setting.operational_mode = normalize_operational_mode(setting.operational_mode)
        return setting

    now = datetime.now(timezone.utc)
    setting = WorkspaceControlSetting(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        is_paused=False,
        operational_mode=DEFAULT_OPERATIONAL_MODE,
        last_mode_change_at=now,
        channels_json=_json_dumps(dict(_DEFAULT_CHANNELS)),
    )
    session.add(setting)
    session.commit()
    return setting


def parse_channels(setting: WorkspaceControlSetting) -> Dict[str, bool]:
    channels = _json_load_dict(setting.channels_json)
    merged = dict(_DEFAULT_CHANNELS)
    for channel, enabled in channels.items():
        if channel in merged:
            merged[channel] = bool(enabled)
    return merged


def set_channel_state(
    session: Session,
    *,
    workspace_id: str,
    channel: str,
    enabled: bool,
) -> WorkspaceControlSetting:
    normalized = channel.strip().lower()
    if normalized not in _ALLOWED_CHANNELS:
        raise ValueError("unsupported_channel")

    setting = get_or_create_control_setting(session, workspace_id=workspace_id)
    channels = parse_channels(setting)
    channels[normalized] = bool(enabled)
    setting.channels_json = _json_dumps(channels)
    setting.updated_at = datetime.now(timezone.utc)
    session.commit()
    return setting


def set_pause_state(session: Session, *, workspace_id: str, paused: bool) -> WorkspaceControlSetting:
    setting = get_or_create_control_setting(session, workspace_id=workspace_id)
    setting.is_paused = bool(paused)
    setting.updated_at = datetime.now(timezone.utc)
    session.commit()
    return setting


def get_workspace_operational_mode(
    session: Session,
    *,
    workspace_id: str,
    redis_client: Any | None = None,
) -> str:
    if redis_client is not None:
        cached = get_workspace_mode_cached(redis_client, workspace_id=workspace_id)
        if cached in VALID_OPERATIONAL_MODES:
            return cached

    setting = get_or_create_control_setting(session, workspace_id=workspace_id)
    mode = normalize_operational_mode(setting.operational_mode)
    if setting.operational_mode != mode:
        setting.operational_mode = mode
        setting.updated_at = datetime.now(timezone.utc)
        session.commit()

    if redis_client is not None:
        set_workspace_mode_cached(redis_client, workspace_id=workspace_id, mode=mode)
    return mode


def set_operational_mode(
    session: Session,
    *,
    workspace_id: str,
    mode: str,
    changed_by_user_id: str | None,
    redis_client: Any | None = None,
) -> WorkspaceControlSetting:
    normalized = str(mode or "").strip().lower()
    if normalized not in VALID_OPERATIONAL_MODES:
        raise ValueError("invalid_operational_mode")

    setting = get_or_create_control_setting(session, workspace_id=workspace_id)
    now = datetime.now(timezone.utc)
    setting.operational_mode = normalized
    setting.last_mode_change_at = now
    setting.mode_changed_by_user_id = changed_by_user_id
    setting.updated_at = now
    session.commit()

    if redis_client is not None:
        set_workspace_mode_cached(redis_client, workspace_id=workspace_id, mode=normalized)

    return setting


def set_limit_override(
    session: Session,
    *,
    workspace_id: str,
    kind: str,
    value: int,
    ttl_seconds: int,
) -> WorkspaceControlSetting:
    normalized_kind = kind.strip().lower()
    if normalized_kind not in {"replies", "posts"}:
        raise ValueError("unsupported_limit_kind")
    if value < 0:
        raise ValueError("limit_override_must_be_non_negative")

    setting = get_or_create_control_setting(session, workspace_id=workspace_id)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(1, ttl_seconds))
    if normalized_kind == "replies":
        setting.reply_limit_override = value
    else:
        setting.post_limit_override = value
    setting.limit_override_expires_at = expires_at
    setting.updated_at = datetime.now(timezone.utc)
    session.commit()
    return setting


def active_limit_override(
    setting: Optional[WorkspaceControlSetting],
    *,
    action: str,
    now: Optional[datetime] = None,
) -> Optional[int]:
    if setting is None:
        return None

    reference = now or datetime.now(timezone.utc)
    expires_at = setting.limit_override_expires_at
    if expires_at is None:
        return None
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= reference:
        return None

    if action == "publish_reply":
        return setting.reply_limit_override
    if action in {"publish_post", "publish_email", "publish_blog", "publish_instagram"}:
        return setting.post_limit_override
    return None


def create_admin_action(
    session: Session,
    *,
    workspace_id: str,
    actor_user_id: Optional[str],
    telegram_user_id: str,
    command: str,
    payload: Dict[str, Any],
    status: str,
    result_summary: Optional[str],
    error_message: Optional[str],
    duration_ms: Optional[int],
    request_id: Optional[str],
    idempotency_key: Optional[str],
) -> AdminAction:
    action = AdminAction(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        telegram_user_id=telegram_user_id,
        command=command,
        payload_json=_json_dumps(payload),
        status=status,
        result_summary=result_summary,
        error_message=(error_message[:255] if error_message else None),
        duration_ms=duration_ms,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
    session.add(action)
    session.commit()
    return action


def create_queue_item(
    session: Session,
    *,
    workspace_id: str,
    item_type: str,
    content_text: str,
    source_kind: Optional[str],
    source_ref_id: Optional[str],
    intent: Optional[str],
    opportunity_score: Optional[int],
    metadata: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
) -> ApprovalQueueItem:
    normalized_type = item_type.strip().lower()
    if normalized_type not in _ALLOWED_QUEUE_TYPES:
        raise ValueError("unsupported_queue_item_type")

    existing = None
    if idempotency_key:
        existing = session.scalar(
            select(ApprovalQueueItem).where(
                ApprovalQueueItem.workspace_id == workspace_id,
                ApprovalQueueItem.idempotency_key == idempotency_key,
            )
        )
    if existing is not None:
        return existing

    item = ApprovalQueueItem(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        item_type=normalized_type,
        status="pending",
        content_text=content_text,
        source_kind=source_kind,
        source_ref_id=source_ref_id,
        intent=intent,
        opportunity_score=opportunity_score,
        metadata_json=_json_dumps(metadata or {}),
        idempotency_key=idempotency_key,
    )
    session.add(item)
    session.commit()
    return item


def get_queue_item(session: Session, *, workspace_id: str, queue_item_id: str) -> Optional[ApprovalQueueItem]:
    return session.scalar(
        select(ApprovalQueueItem).where(
            ApprovalQueueItem.workspace_id == workspace_id,
            ApprovalQueueItem.id == queue_item_id,
        )
    )


def list_pending_queue_items(session: Session, *, workspace_id: str, limit: int = 5) -> List[ApprovalQueueItem]:
    safe_limit = max(1, min(limit, 50))
    statement = (
        select(ApprovalQueueItem)
        .where(
            ApprovalQueueItem.workspace_id == workspace_id,
            ApprovalQueueItem.status == "pending",
        )
        .order_by(desc(ApprovalQueueItem.created_at))
        .limit(safe_limit)
    )
    return list(session.scalars(statement).all())


def mark_queue_item_rejected(
    session: Session,
    *,
    item: ApprovalQueueItem,
    rejected_by_user_id: str,
) -> ApprovalQueueItem:
    now = datetime.now(timezone.utc)
    item.status = "rejected"
    item.rejected_by_user_id = rejected_by_user_id
    item.rejected_at = now
    item.updated_at = now
    session.commit()
    return item


def mark_queue_item_approved(
    session: Session,
    *,
    item: ApprovalQueueItem,
    approved_by_user_id: str,
) -> ApprovalQueueItem:
    now = datetime.now(timezone.utc)
    item.status = "approved"
    item.approved_by_user_id = approved_by_user_id
    item.approved_at = now
    item.updated_at = now
    session.commit()
    return item


def mark_queue_item_publishing(
    session: Session,
    *,
    item: ApprovalQueueItem,
    approved_by_user_id: Optional[str] = None,
) -> ApprovalQueueItem:
    now = datetime.now(timezone.utc)
    item.status = "publishing"
    if approved_by_user_id:
        item.approved_by_user_id = approved_by_user_id
    if item.approved_at is None:
        item.approved_at = now
    item.updated_at = now
    session.commit()
    return item


def mark_queue_item_published(
    session: Session,
    *,
    item: ApprovalQueueItem,
    external_post_id: Optional[str],
) -> ApprovalQueueItem:
    now = datetime.now(timezone.utc)
    item.status = "published"
    item.published_post_id = external_post_id
    item.error_message = None
    item.updated_at = now
    session.commit()
    return item


def mark_queue_item_failed(
    session: Session,
    *,
    item: ApprovalQueueItem,
    error_message: str,
) -> ApprovalQueueItem:
    now = datetime.now(timezone.utc)
    item.status = "failed"
    item.error_message = error_message[:255]
    item.updated_at = now
    session.commit()
    return item


def parse_queue_metadata(item: ApprovalQueueItem) -> Dict[str, Any]:
    return _json_load_dict(item.metadata_json)


def create_pipeline_run(
    session: Session,
    *,
    workspace_id: str,
    pipeline_name: str,
    dry_run: bool,
    request_id: Optional[str],
    idempotency_key: Optional[str],
    actor_user_id: Optional[str],
    telegram_user_id: Optional[str],
) -> PipelineRun:
    run = PipelineRun(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        pipeline_name=pipeline_name,
        status="started",
        dry_run=dry_run,
        request_id=request_id,
        idempotency_key=idempotency_key,
        actor_user_id=actor_user_id,
        telegram_user_id=telegram_user_id,
        result_json="{}",
    )
    session.add(run)
    session.commit()
    return run


def get_pipeline_run_by_idempotency(
    session: Session,
    *,
    workspace_id: str,
    pipeline_name: str,
    idempotency_key: Optional[str],
) -> Optional[PipelineRun]:
    if not idempotency_key:
        return None
    return session.scalar(
        select(PipelineRun).where(
            PipelineRun.workspace_id == workspace_id,
            PipelineRun.pipeline_name == pipeline_name,
            PipelineRun.idempotency_key == idempotency_key,
        )
    )


def finish_pipeline_run(
    session: Session,
    *,
    run: PipelineRun,
    status: str,
    result: Optional[Dict[str, Any]],
    error_message: Optional[str],
) -> PipelineRun:
    run.status = status
    run.result_json = _json_dumps(result or {})
    run.error_message = error_message[:255] if error_message else None
    run.finished_at = datetime.now(timezone.utc)
    session.commit()
    return run


def latest_pipeline_runs(session: Session, *, workspace_id: str, limit: int = 20) -> List[PipelineRun]:
    safe_limit = max(1, min(limit, 100))
    statement = (
        select(PipelineRun)
        .where(PipelineRun.workspace_id == workspace_id)
        .order_by(desc(PipelineRun.created_at))
        .limit(safe_limit)
    )
    return list(session.scalars(statement).all())
