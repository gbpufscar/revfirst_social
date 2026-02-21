"""Status command handler."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List

from sqlalchemy import desc, select

from src.control.command_schema import ControlResponse
from src.control.formatters import format_recent_errors
from src.control.services import (
    get_or_create_control_setting,
    get_workspace_operational_mode,
    latest_pipeline_runs,
    parse_channels,
)
from src.control.state import is_global_kill_switch, is_workspace_paused
from src.core.runtime import load_runtime_config
from src.storage.models import AdminAction, PipelineRun, PublishAuditLog

if TYPE_CHECKING:
    from src.control.command_router import CommandContext


def _to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _latest_pipeline_summary(runs: List[PipelineRun]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for run in runs:
        pipeline = run.pipeline_name
        if pipeline in result:
            continue
        result[pipeline] = {
            "status": run.status,
            "dry_run": bool(run.dry_run),
            "created_at": _to_iso(run.created_at),
            "finished_at": _to_iso(run.finished_at),
            "error": run.error_message,
        }
    return result


def _fetch_recent_errors(context: "CommandContext") -> List[Dict[str, Any]]:
    session = context.session
    workspace_id = context.envelope.workspace_id

    admin_errors = session.scalars(
        select(AdminAction)
        .where(
            AdminAction.workspace_id == workspace_id,
            AdminAction.status.in_(["error", "unauthorized"]),
        )
        .order_by(desc(AdminAction.created_at))
        .limit(2)
    ).all()

    pipeline_errors = session.scalars(
        select(PipelineRun)
        .where(
            PipelineRun.workspace_id == workspace_id,
            PipelineRun.status == "failed",
        )
        .order_by(desc(PipelineRun.created_at))
        .limit(2)
    ).all()

    publish_errors = session.scalars(
        select(PublishAuditLog)
        .where(
            PublishAuditLog.workspace_id == workspace_id,
            PublishAuditLog.status == "failed",
        )
        .order_by(desc(PublishAuditLog.created_at))
        .limit(2)
    ).all()

    errors: List[Dict[str, Any]] = []
    for item in admin_errors:
        errors.append(
            {
                "source": "admin_action",
                "message": item.error_message or item.result_summary or item.command,
                "created_at": _to_iso(item.created_at),
            }
        )
    for item in pipeline_errors:
        errors.append(
            {
                "source": f"pipeline:{item.pipeline_name}",
                "message": item.error_message or "pipeline_failed",
                "created_at": _to_iso(item.created_at),
            }
        )
    for item in publish_errors:
        errors.append(
            {
                "source": "publish",
                "message": item.error_message or "publish_failed",
                "created_at": _to_iso(item.created_at),
            }
        )

    errors.sort(key=lambda row: row.get("created_at") or "", reverse=True)
    return errors[:5]


def handle(context: "CommandContext") -> ControlResponse:
    workspace_id = context.envelope.workspace_id
    runtime = load_runtime_config()

    runs = latest_pipeline_runs(context.session, workspace_id=workspace_id, limit=20)
    settings = get_or_create_control_setting(context.session, workspace_id=workspace_id)

    scheduler_lock_key = f"revfirst:{workspace_id}:scheduler:lock"
    run_lock_pattern = f"revfirst:{workspace_id}:control:run:*:lock"
    active_locks = []

    if context.redis_client.exists(scheduler_lock_key):
        active_locks.append(scheduler_lock_key)

    run_lock_keys = context.redis_client.keys(run_lock_pattern)
    for key in run_lock_keys:
        active_locks.append(str(key))

    paused_redis = is_workspace_paused(context.redis_client, workspace_id=workspace_id)
    global_kill = is_global_kill_switch(context.redis_client)
    mode = get_workspace_operational_mode(
        context.session,
        workspace_id=workspace_id,
        redis_client=context.redis_client,
    )

    data = {
        "workspace_id": workspace_id,
        "mode": mode,
        "single_workspace_mode": runtime.single_workspace_mode,
        "primary_workspace_id": runtime.primary_workspace_id,
        "paused": bool(settings.is_paused) or paused_redis,
        "global_kill_switch": global_kill,
        "channels": parse_channels(settings),
        "last_runs": _latest_pipeline_summary(runs),
        "active_locks": active_locks,
        "recent_errors": format_recent_errors(_fetch_recent_errors(context)),
    }

    return ControlResponse(success=True, message="status_ok", data=data)
