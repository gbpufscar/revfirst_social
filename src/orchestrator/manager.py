"""CLI entrypoint to run one scheduler cycle."""

from __future__ import annotations

from dataclasses import asdict
import argparse
import json
from typing import Any, Dict

from src.core.config import get_settings
from src.integrations.x.x_client import get_x_client
from src.orchestrator.locks import WorkspaceLockManager
from src.orchestrator.pipeline import run_workspace_pipeline
from src.orchestrator.scheduler import SchedulerRunResult, WorkspaceScheduler
from src.storage.db import get_session_factory, load_models
from src.storage.redis_client import get_client as get_redis_client


def run_scheduler_once(*, limit: int | None = None) -> SchedulerRunResult:
    settings = get_settings()
    load_models()
    x_client = get_x_client()

    scheduler = WorkspaceScheduler(
        session_factory=get_session_factory(),
        lock_manager=WorkspaceLockManager(
            get_redis_client(),
            ttl_seconds=settings.scheduler_workspace_lock_ttl_seconds,
        ),
        pipeline_runner=lambda session, workspace_id: run_workspace_pipeline(
            session,
            workspace_id=workspace_id,
            x_client=x_client,
        ),
    )
    default_limit = settings.scheduler_max_workspaces_per_run
    return scheduler.run_once(limit=limit or default_limit)


def _result_to_dict(result: SchedulerRunResult) -> Dict[str, Any]:
    payload = asdict(result)
    payload["runs"] = [asdict(run) for run in result.runs]
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Run RevFirst scheduler once.")
    parser.add_argument("--limit", type=int, default=None, help="Max active workspaces to process.")
    args = parser.parse_args()

    result = run_scheduler_once(limit=args.limit)
    print(json.dumps(_result_to_dict(result), ensure_ascii=True, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()

