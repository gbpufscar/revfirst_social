"""Multi-tenant scheduler with per-workspace Redis lock isolation."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Callable, Dict, Iterable, List, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.core.logger import get_logger
from src.core.observability import capture_exception, sentry_scope
from src.core.runtime import load_runtime_config
from src.control.services import scheduler_enabled_for_mode
from src.orchestrator.locks import WorkspaceLockManager
from src.storage.models import Workspace, WorkspaceEvent
from src.storage.tenant import reset_workspace_context, set_workspace_context


ACTIVE_WORKSPACE_STATUSES = ("active", "trialing")
PipelineRunner = Callable[[Session, str], Mapping[str, Any]]
WorkspacePauseChecker = Callable[[str], bool]
GlobalKillSwitchChecker = Callable[[], bool]
WorkspaceModeResolver = Callable[[str], str]

logger = get_logger("revfirst.orchestrator.scheduler")


def _json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True)


@dataclass(frozen=True)
class WorkspaceRunSummary:
    workspace_id: str
    status: str
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SchedulerRunResult:
    total_active_workspaces: int
    executed: int
    skipped_locked: int
    failed: int
    runs: List[WorkspaceRunSummary]


class WorkspaceScheduler:
    """Run per-workspace pipelines with lock and DB context isolation."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker,
        lock_manager: WorkspaceLockManager,
        pipeline_runner: PipelineRunner,
        workspace_pause_checker: WorkspacePauseChecker | None = None,
        global_kill_switch_checker: GlobalKillSwitchChecker | None = None,
        workspace_mode_resolver: WorkspaceModeResolver | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._lock_manager = lock_manager
        self._pipeline_runner = pipeline_runner
        self._workspace_pause_checker = workspace_pause_checker or (lambda workspace_id: False)
        self._global_kill_switch_checker = global_kill_switch_checker or (lambda: False)
        self._workspace_mode_resolver = workspace_mode_resolver or (lambda workspace_id: "semi_autonomous")

    def list_active_workspace_ids(self, *, limit: int | None = None) -> List[str]:
        runtime = load_runtime_config()
        if runtime.single_workspace_mode:
            if not runtime.primary_workspace_id:
                logger.error("single_workspace_mode_enabled_without_primary_workspace")
                return []
            return [runtime.primary_workspace_id]

        with self._session_factory() as session:
            statement = (
                select(Workspace.id)
                .where(Workspace.subscription_status.in_(ACTIVE_WORKSPACE_STATUSES))
                .order_by(Workspace.created_at.asc())
            )
            if limit is not None:
                safe_limit = max(1, limit)
                statement = statement.limit(safe_limit)
            return [str(workspace_id) for workspace_id in session.scalars(statement).all()]

    def run_once(self, *, workspace_ids: Iterable[str] | None = None, limit: int | None = None) -> SchedulerRunResult:
        selected_ids = list(workspace_ids) if workspace_ids is not None else self.list_active_workspace_ids(limit=limit)

        executed = 0
        skipped_locked = 0
        failed = 0
        runs: List[WorkspaceRunSummary] = []

        if self._global_kill_switch_checker():
            for workspace_id in selected_ids:
                details = {"reason": "global_kill_switch_enabled"}
                runs.append(WorkspaceRunSummary(workspace_id=workspace_id, status="skipped_paused", details=details))
                self._record_scheduler_event(workspace_id=workspace_id, status="skipped_paused", details=details)
                logger.warning("workspace_scheduler_skipped_global_kill_switch", workspace_id=workspace_id)
            return SchedulerRunResult(
                total_active_workspaces=len(selected_ids),
                executed=0,
                skipped_locked=0,
                failed=0,
                runs=runs,
            )

        for workspace_id in selected_ids:
            workspace_mode = self._workspace_mode_resolver(workspace_id)
            if not scheduler_enabled_for_mode(workspace_mode):
                details = {"reason": "mode_blocks_scheduler", "mode": workspace_mode}
                runs.append(WorkspaceRunSummary(workspace_id=workspace_id, status="skipped_mode", details=details))
                self._record_scheduler_event(workspace_id=workspace_id, status="skipped_mode", details=details)
                logger.info("workspace_scheduler_skipped_mode", workspace_id=workspace_id, mode=workspace_mode)
                continue

            if self._workspace_pause_checker(workspace_id):
                details = {"reason": "workspace_paused"}
                runs.append(WorkspaceRunSummary(workspace_id=workspace_id, status="skipped_paused", details=details))
                self._record_scheduler_event(workspace_id=workspace_id, status="skipped_paused", details=details)
                logger.info("workspace_scheduler_skipped_paused", workspace_id=workspace_id)
                continue

            lock = self._lock_manager.acquire(workspace_id)
            if lock is None:
                skipped_locked += 1
                details = {"reason": "workspace_lock_exists"}
                runs.append(WorkspaceRunSummary(workspace_id=workspace_id, status="skipped_locked", details=details))
                self._record_scheduler_event(workspace_id=workspace_id, status="skipped_locked", details=details)
                logger.info("workspace_scheduler_skipped_locked", workspace_id=workspace_id)
                continue

            try:
                with sentry_scope(workspace_id=workspace_id):
                    details = self._run_workspace_pipeline(workspace_id)
                    executed += 1
                    runs.append(WorkspaceRunSummary(workspace_id=workspace_id, status="executed", details=details))
                    self._record_scheduler_event(workspace_id=workspace_id, status="executed", details=details)
                    logger.info("workspace_scheduler_executed", workspace_id=workspace_id)
            except Exception as exc:
                failed += 1
                details = {"error": str(exc)}
                runs.append(WorkspaceRunSummary(workspace_id=workspace_id, status="failed", details=details))
                self._record_scheduler_event(workspace_id=workspace_id, status="failed", details=details)
                capture_exception(exc)
                logger.error("workspace_scheduler_failed", workspace_id=workspace_id, error=str(exc))
            finally:
                lock.release()

        return SchedulerRunResult(
            total_active_workspaces=len(selected_ids),
            executed=executed,
            skipped_locked=skipped_locked,
            failed=failed,
            runs=runs,
        )

    def _run_workspace_pipeline(self, workspace_id: str) -> Dict[str, Any]:
        with self._session_factory() as session:
            set_workspace_context(session, workspace_id)
            try:
                result = self._pipeline_runner(session, workspace_id)
                if isinstance(result, Mapping):
                    return dict(result)
                return {}
            finally:
                reset_workspace_context(session)

    def _record_scheduler_event(self, *, workspace_id: str, status: str, details: Mapping[str, Any]) -> None:
        with self._session_factory() as session:
            set_workspace_context(session, workspace_id)
            try:
                payload = {"status": status, "details": dict(details)}
                session.add(
                    WorkspaceEvent(
                        workspace_id=workspace_id,
                        event_type="scheduler_workspace_run",
                        payload_json=_json(payload),
                    )
                )
                session.commit()
            finally:
                reset_workspace_context(session)
