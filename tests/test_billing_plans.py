from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, sessionmaker

from src.billing.plans import check_plan_limit, load_plans, record_usage
from src.storage.db import Base, load_models
from src.storage.models import Workspace, WorkspaceControlSetting


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
    assert plans["free"]["max_emails_per_day"] == 1
    assert plans["pro"]["max_emails_per_day"] == 5
    assert plans["free"]["max_blogs_per_day"] == 1
    assert plans["pro"]["max_blogs_per_day"] == 3


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


def test_check_plan_limit_prefers_active_control_override(monkeypatch) -> None:
    monkeypatch.setenv("PLANS_FILE_PATH", "config/plans.yaml")
    load_plans.cache_clear()

    session = _build_session()
    try:
        workspace = Workspace(
            id=str(uuid.uuid4()),
            name="billing-override",
            plan="free",
            subscription_status="active",
        )
        session.add(workspace)
        session.commit()

        session.add(
            WorkspaceControlSetting(
                id=str(uuid.uuid4()),
                workspace_id=workspace.id,
                is_paused=False,
                channels_json='{"x":true,"email":false,"blog":false,"instagram":false}',
                reply_limit_override=8,
                limit_override_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        session.commit()

        for _ in range(6):
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
        assert decision.allowed is True
        assert decision.limit == 8
        assert decision.used == 6
        assert decision.plan.endswith(":override")
    finally:
        session.close()


def test_check_plan_limit_email_prefers_active_post_override(monkeypatch) -> None:
    monkeypatch.setenv("PLANS_FILE_PATH", "config/plans.yaml")
    load_plans.cache_clear()

    session = _build_session()
    try:
        workspace = Workspace(
            id=str(uuid.uuid4()),
            name="billing-email-override",
            plan="free",
            subscription_status="active",
        )
        session.add(workspace)
        session.commit()

        session.add(
            WorkspaceControlSetting(
                id=str(uuid.uuid4()),
                workspace_id=workspace.id,
                is_paused=False,
                channels_json='{"x":true,"email":true,"blog":false,"instagram":false}',
                post_limit_override=3,
                limit_override_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        session.commit()

        for _ in range(2):
            record_usage(
                session,
                workspace_id=workspace.id,
                action="publish_email",
                amount=1,
                payload={"source": "test"},
            )
        session.commit()

        decision = check_plan_limit(
            session,
            workspace_id=workspace.id,
            action="publish_email",
            requested=1,
            usage_date=datetime.now(timezone.utc).date(),
        )
        assert decision.allowed is True
        assert decision.limit == 3
        assert decision.used == 2
        assert decision.plan.endswith(":override")
    finally:
        session.close()


def test_check_plan_limit_blog_prefers_active_post_override(monkeypatch) -> None:
    monkeypatch.setenv("PLANS_FILE_PATH", "config/plans.yaml")
    load_plans.cache_clear()

    session = _build_session()
    try:
        workspace = Workspace(
            id=str(uuid.uuid4()),
            name="billing-blog-override",
            plan="free",
            subscription_status="active",
        )
        session.add(workspace)
        session.commit()

        session.add(
            WorkspaceControlSetting(
                id=str(uuid.uuid4()),
                workspace_id=workspace.id,
                is_paused=False,
                channels_json='{"x":true,"email":false,"blog":true,"instagram":false}',
                post_limit_override=2,
                limit_override_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        session.commit()

        record_usage(
            session,
            workspace_id=workspace.id,
            action="publish_blog",
            amount=1,
            payload={"source": "test"},
        )
        session.commit()

        decision = check_plan_limit(
            session,
            workspace_id=workspace.id,
            action="publish_blog",
            requested=1,
            usage_date=datetime.now(timezone.utc).date(),
        )
        assert decision.allowed is True
        assert decision.limit == 2
        assert decision.used == 1
        assert decision.plan.endswith(":override")
    finally:
        session.close()
