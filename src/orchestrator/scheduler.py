"""Multi-tenant scheduler with per-workspace Redis lock isolation."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Callable, Dict, Iterable, List, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.core.logger import get_logger
from src.orchestrator.locks import WorkspaceLockManager
from src.storage.models import Workspace, WorkspaceEvent
from src.storage.tenant import reset_workspace_context, set_workspace_context


ACTIVE_WORKSPACE_STATUSES = ("active", "trialing")
PipelineRunner = Callable[[Session, str], Mapping[str, Any]]

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
    ) -> None:
        self._session_factory = session_factory
        self._lock_manager = lock_manager
        self._pipeline_runner = pipeline_runner

    def list_active_workspace_ids(self, *, limit: int | None = None) -> List[str]:
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

        for workspace_id in selected_ids:
            lock = self._lock_manager.acquire(workspace_id)
            if lock is None:
                skipped_locked += 1
                details = {"reason": "workspace_lock_exists"}
                runs.append(WorkspaceRunSummary(workspace_id=workspace_id, status="skipped_locked", details=details))
                self._record_scheduler_event(workspace_id=workspace_id, status="skipped_locked", details=details)
                logger.info("workspace_scheduler_skipped_locked", workspace_id=workspace_id)
                continue

            try:
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

