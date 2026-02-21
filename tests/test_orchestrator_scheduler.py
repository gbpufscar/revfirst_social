from __future__ import annotations

import json
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

import src.orchestrator.scheduler as scheduler_module
from src.core.runtime import RuntimeConfig
from src.orchestrator.locks import WorkspaceLockManager
from src.orchestrator.scheduler import WorkspaceScheduler
from src.storage.db import Base, load_models
from src.storage.models import Workspace, WorkspaceControlSetting, WorkspaceEvent


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None):  # noqa: ARG002
        if nx and key in self._store:
            return False
        self._store[key] = value
        return True

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def eval(self, script: str, keys_count: int, key: str, token: str):  # noqa: ARG002
        if keys_count != 1:
            raise ValueError("Expected one key")
        if self._store.get(key) == token:
            del self._store[key]
            return 1
        return 0


def _build_sqlite_session_factory():
    load_models()
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _create_workspace(session, *, name: str, status: str = "active") -> Workspace:
    workspace = Workspace(
        id=str(uuid.uuid4()),
        name=name,
        plan="free",
        subscription_status=status,
    )
    session.add(workspace)
    session.commit()
    return workspace


def test_scheduler_runs_only_active_workspaces_and_records_events() -> None:
    session_factory = _build_sqlite_session_factory()
    fake_redis = _FakeRedis()
    lock_manager = WorkspaceLockManager(fake_redis, ttl_seconds=60)
    run_order: list[str] = []

    with session_factory() as seed:
        active_a = _create_workspace(seed, name=f"active-a-{uuid.uuid4()}", status="active")
        active_b = _create_workspace(seed, name=f"active-b-{uuid.uuid4()}", status="trialing")
        _create_workspace(seed, name=f"inactive-{uuid.uuid4()}", status="inactive")

    def pipeline_runner(session, workspace_id: str):
        del session
        run_order.append(workspace_id)
        return {"pipeline": "ok"}

    scheduler = WorkspaceScheduler(
        session_factory=session_factory,
        lock_manager=lock_manager,
        pipeline_runner=pipeline_runner,
    )
    result = scheduler.run_once()

    assert result.total_active_workspaces == 2
    assert result.executed == 2
    assert result.skipped_locked == 0
    assert result.failed == 0
    assert set(run_order) == {active_a.id, active_b.id}

    with session_factory() as verify:
        events = verify.scalars(
            select(WorkspaceEvent).where(WorkspaceEvent.event_type == "scheduler_workspace_run")
        ).all()
        assert len(events) == 2
        statuses = [json.loads(event.payload_json)["status"] for event in events]
        assert statuses.count("executed") == 2


def test_scheduler_skips_workspace_when_lock_exists() -> None:
    session_factory = _build_sqlite_session_factory()
    fake_redis = _FakeRedis()
    lock_manager = WorkspaceLockManager(fake_redis, ttl_seconds=60)

    with session_factory() as seed:
        workspace = _create_workspace(seed, name=f"locked-{uuid.uuid4()}", status="active")

    key = lock_manager.lock_key(workspace.id)
    fake_redis.set(key, "external-owner-token", nx=True, ex=60)

    called = {"count": 0}

    def pipeline_runner(session, workspace_id: str):  # noqa: ARG001
        called["count"] += 1
        return {}

    scheduler = WorkspaceScheduler(
        session_factory=session_factory,
        lock_manager=lock_manager,
        pipeline_runner=pipeline_runner,
    )
    result = scheduler.run_once(workspace_ids=[workspace.id])

    assert result.total_active_workspaces == 1
    assert result.executed == 0
    assert result.skipped_locked == 1
    assert result.failed == 0
    assert called["count"] == 0

    with session_factory() as verify:
        events = verify.scalars(
            select(WorkspaceEvent).where(WorkspaceEvent.workspace_id == workspace.id)
        ).all()
        assert len(events) == 1
        payload = json.loads(events[0].payload_json)
        assert payload["status"] == "skipped_locked"


def test_scheduler_releases_lock_after_failure() -> None:
    session_factory = _build_sqlite_session_factory()
    fake_redis = _FakeRedis()
    lock_manager = WorkspaceLockManager(fake_redis, ttl_seconds=60)

    with session_factory() as seed:
        workspace = _create_workspace(seed, name=f"failure-{uuid.uuid4()}", status="active")

    def failing_runner(session, workspace_id: str):  # noqa: ARG001
        raise RuntimeError("pipeline failed")

    scheduler_failure = WorkspaceScheduler(
        session_factory=session_factory,
        lock_manager=lock_manager,
        pipeline_runner=failing_runner,
    )
    failed_result = scheduler_failure.run_once(workspace_ids=[workspace.id])
    assert failed_result.failed == 1
    assert fake_redis.get(lock_manager.lock_key(workspace.id)) is None

    def success_runner(session, workspace_id: str):  # noqa: ARG001
        return {"recovery": True}

    scheduler_success = WorkspaceScheduler(
        session_factory=session_factory,
        lock_manager=lock_manager,
        pipeline_runner=success_runner,
    )
    success_result = scheduler_success.run_once(workspace_ids=[workspace.id])

    assert success_result.executed == 1
    assert success_result.failed == 0

    with session_factory() as verify:
        events = verify.scalars(
            select(WorkspaceEvent).where(WorkspaceEvent.workspace_id == workspace.id)
        ).all()
        assert len(events) == 2
        statuses = [json.loads(event.payload_json)["status"] for event in events]
        assert "failed" in statuses
        assert "executed" in statuses


def test_scheduler_honors_single_workspace_mode(monkeypatch) -> None:
    session_factory = _build_sqlite_session_factory()
    fake_redis = _FakeRedis()
    lock_manager = WorkspaceLockManager(fake_redis, ttl_seconds=60)
    run_order: list[str] = []

    with session_factory() as seed:
        primary = _create_workspace(seed, name=f"primary-{uuid.uuid4()}", status="active")
        _create_workspace(seed, name=f"secondary-{uuid.uuid4()}", status="active")

    monkeypatch.setattr(
        scheduler_module,
        "load_runtime_config",
        lambda: RuntimeConfig(single_workspace_mode=True, primary_workspace_id=primary.id),
    )

    def pipeline_runner(session, workspace_id: str):  # noqa: ARG001
        run_order.append(workspace_id)
        return {"pipeline": "ok"}

    scheduler = WorkspaceScheduler(
        session_factory=session_factory,
        lock_manager=lock_manager,
        pipeline_runner=pipeline_runner,
    )
    result = scheduler.run_once()

    assert result.total_active_workspaces == 1
    assert result.executed == 1
    assert run_order == [primary.id]


def test_scheduler_single_workspace_mode_without_primary_returns_empty(monkeypatch) -> None:
    session_factory = _build_sqlite_session_factory()
    fake_redis = _FakeRedis()
    lock_manager = WorkspaceLockManager(fake_redis, ttl_seconds=60)

    with session_factory() as seed:
        _create_workspace(seed, name=f"active-{uuid.uuid4()}", status="active")

    monkeypatch.setattr(
        scheduler_module,
        "load_runtime_config",
        lambda: RuntimeConfig.model_construct(single_workspace_mode=True, primary_workspace_id=None),
    )

    scheduler = WorkspaceScheduler(
        session_factory=session_factory,
        lock_manager=lock_manager,
        pipeline_runner=lambda session, workspace_id: {},  # noqa: ARG005
    )
    result = scheduler.run_once()

    assert result.total_active_workspaces == 0
    assert result.executed == 0


def test_scheduler_skips_workspaces_when_mode_blocks_scheduler() -> None:
    session_factory = _build_sqlite_session_factory()
    fake_redis = _FakeRedis()
    lock_manager = WorkspaceLockManager(fake_redis, ttl_seconds=60)
    run_order: list[str] = []

    with session_factory() as seed:
        ws_manual = _create_workspace(seed, name=f"manual-{uuid.uuid4()}", status="active")
        ws_containment = _create_workspace(seed, name=f"containment-{uuid.uuid4()}", status="active")
        ws_semi = _create_workspace(seed, name=f"semi-{uuid.uuid4()}", status="active")
        seed.add(
            WorkspaceControlSetting(
                id=str(uuid.uuid4()),
                workspace_id=ws_manual.id,
                is_paused=False,
                operational_mode="manual",
                channels_json='{"blog":false,"email":false,"instagram":false,"x":true}',
            )
        )
        seed.add(
            WorkspaceControlSetting(
                id=str(uuid.uuid4()),
                workspace_id=ws_containment.id,
                is_paused=False,
                operational_mode="containment",
                channels_json='{"blog":false,"email":false,"instagram":false,"x":true}',
            )
        )
        seed.commit()

    scheduler = WorkspaceScheduler(
        session_factory=session_factory,
        lock_manager=lock_manager,
        pipeline_runner=lambda session, workspace_id: run_order.append(workspace_id) or {"pipeline": "ok"},  # noqa: ARG005
        workspace_mode_resolver=lambda workspace_id: (
            "manual"
            if workspace_id == ws_manual.id
            else ("containment" if workspace_id == ws_containment.id else "semi_autonomous")
        ),
    )
    result = scheduler.run_once(workspace_ids=[ws_manual.id, ws_containment.id, ws_semi.id])

    assert result.total_active_workspaces == 3
    assert result.executed == 1
    assert result.failed == 0
    assert run_order == [ws_semi.id]
    assert sorted(summary.status for summary in result.runs).count("skipped_mode") == 2
