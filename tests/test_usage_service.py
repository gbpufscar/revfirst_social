from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, sessionmaker

from src.app.usage_service import (
    PlanLimitExceededError,
    consume_workspace_action,
    get_workspace_daily_usage,
)
from src.billing.plans import load_plans
from src.storage.db import Base, load_models
from src.storage.models import UsageLog, Workspace, WorkspaceDailyUsage


def _build_session() -> Session:
    load_models()
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    return factory()


def test_consume_workspace_action_persists_log_and_aggregation(monkeypatch) -> None:
    monkeypatch.setenv("PLANS_FILE_PATH", "config/plans.yaml")
    load_plans.cache_clear()

    session = _build_session()
    try:
        workspace = Workspace(
            id=str(uuid.uuid4()),
            name="usage-service-ok",
            plan="free",
            subscription_status="active",
        )
        session.add(workspace)
        session.commit()

        result = consume_workspace_action(
            session,
            workspace_id=workspace.id,
            action="publish_reply",
            amount=1,
            payload={"source": "unit-test"},
        )
        assert result.consumed == 1
        assert result.used_after == 1
        assert result.limit == 5

        usage_logs = session.scalars(select(UsageLog).where(UsageLog.workspace_id == workspace.id)).all()
        assert len(usage_logs) == 1

        aggregates = session.scalars(
            select(WorkspaceDailyUsage).where(WorkspaceDailyUsage.workspace_id == workspace.id)
        ).all()
        assert len(aggregates) == 1
        assert aggregates[0].action == "publish_reply"
        assert aggregates[0].count == 1
    finally:
        session.close()


def test_consume_workspace_action_blocks_when_limit_exceeded(monkeypatch) -> None:
    monkeypatch.setenv("PLANS_FILE_PATH", "config/plans.yaml")
    load_plans.cache_clear()

    session = _build_session()
    try:
        workspace = Workspace(
            id=str(uuid.uuid4()),
            name="usage-service-limit",
            plan="free",
            subscription_status="active",
        )
        session.add(workspace)
        session.commit()

        for _ in range(5):
            consume_workspace_action(
                session,
                workspace_id=workspace.id,
                action="publish_reply",
                amount=1,
                payload={"source": "seed"},
            )

        with pytest.raises(PlanLimitExceededError):
            consume_workspace_action(
                session,
                workspace_id=workspace.id,
                action="publish_reply",
                amount=1,
                payload={"source": "overflow"},
            )

        usage_logs = session.scalars(select(UsageLog).where(UsageLog.workspace_id == workspace.id)).all()
        assert len(usage_logs) == 5
    finally:
        session.close()


def test_get_workspace_daily_usage_returns_aggregated_rows(monkeypatch) -> None:
    monkeypatch.setenv("PLANS_FILE_PATH", "config/plans.yaml")
    load_plans.cache_clear()

    session = _build_session()
    try:
        workspace = Workspace(
            id=str(uuid.uuid4()),
            name="usage-service-read",
            plan="pro",
            subscription_status="active",
        )
        session.add(workspace)
        session.commit()

        consume_workspace_action(
            session,
            workspace_id=workspace.id,
            action="publish_reply",
            amount=2,
        )
        consume_workspace_action(
            session,
            workspace_id=workspace.id,
            action="publish_post",
            amount=1,
        )

        rows = get_workspace_daily_usage(session, workspace_id=workspace.id)
        assert len(rows) == 2
        assert [row.action for row in rows] == ["publish_post", "publish_reply"]
    finally:
        session.close()

