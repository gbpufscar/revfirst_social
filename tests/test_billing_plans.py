from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, sessionmaker

from src.billing.plans import check_plan_limit, load_plans, record_usage
from src.storage.db import Base, load_models
from src.storage.models import Workspace


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


def test_load_plans_contains_expected_defaults(monkeypatch) -> None:
    monkeypatch.setenv("PLANS_FILE_PATH", "config/plans.yaml")
    load_plans.cache_clear()
    plans = load_plans()

    assert "free" in plans
    assert "pro" in plans
    assert plans["free"]["max_replies_per_day"] == 5
    assert plans["pro"]["max_posts_per_day"] == 2


def test_check_plan_limit_uses_daily_aggregation(monkeypatch) -> None:
    monkeypatch.setenv("PLANS_FILE_PATH", "config/plans.yaml")
    load_plans.cache_clear()

    session = _build_session()
    try:
        workspace = Workspace(
            id=str(uuid.uuid4()),
            name="billing-limits",
            plan="free",
            subscription_status="active",
        )
        session.add(workspace)
        session.commit()

        for _ in range(5):
            record_usage(
                session,
                workspace_id=workspace.id,
                action="publish_reply",
                amount=1,
                payload={"source": "test"},
            )
        session.commit()

        decision = check_plan_limit(
            session,
            workspace_id=workspace.id,
            action="publish_reply",
            requested=1,
            usage_date=datetime.now(timezone.utc).date(),
        )
        assert decision.allowed is False
        assert decision.limit == 5
        assert decision.used == 5
        assert decision.remaining == 0
    finally:
        session.close()
