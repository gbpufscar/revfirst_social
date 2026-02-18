from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

import src.api.main as api_main
from src.core.config import get_settings
from src.integrations.x.x_client import get_x_client
from src.storage.db import Base, get_session, load_models
from src.storage.models import PublishAuditLog, PublishCooldown, WorkspaceDailyUsage
from src.storage.security import get_token_key


class _FakePublisherXClient:
    def __init__(self) -> None:
        self.counter = 0

    def create_tweet(self, *, access_token: str, text: str, in_reply_to_tweet_id: str | None = None):
        del access_token, text, in_reply_to_tweet_id
        self.counter += 1
        return {"data": {"id": f"tweet-{self.counter}"}}


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


def _bootstrap_workspace(client: TestClient, *, workspace_name: str, owner_email: str, owner_password: str):
    create_response = client.post(
        "/workspaces",
        json={
            "name": workspace_name,
            "owner_email": owner_email,
            "owner_password": owner_password,
        },
    )
    assert create_response.status_code == 201
    workspace_id = create_response.json()["workspace_id"]

    login_response = client.post(
        "/auth/login",
        json={
            "email": owner_email,
            "password": owner_password,
            "workspace_id": workspace_id,
        },
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    manual_token = client.post(
        "/integrations/x/oauth/token/manual",
        json={
            "workspace_id": workspace_id,
            "access_token": "publish-x-access-token",
            "refresh_token": "publish-x-refresh-token",
            "expires_in": 3600,
            "scope": "tweet.read tweet.write users.read",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert manual_token.status_code == 200
    return workspace_id, token


def test_publish_reply_success_creates_audit_usage_and_cooldown(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setenv("PUBLISH_THREAD_COOLDOWN_MINUTES", "60")
    monkeypatch.setenv("PUBLISH_AUTHOR_COOLDOWN_MINUTES", "45")
    get_settings.cache_clear()
    get_token_key.cache_clear()

    session_factory = _build_sqlite_session_factory()
    fake_x = _FakePublisherXClient()

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    api_main.app.dependency_overrides[get_session] = override_get_session
    api_main.app.dependency_overrides[get_x_client] = lambda: fake_x
    try:
        client = TestClient(api_main.app)
        workspace_id, token = _bootstrap_workspace(
            client,
            workspace_name=f"publish-reply-{uuid.uuid4()}",
            owner_email="publish-owner@revfirst.io",
            owner_password="publish-owner-pass-123",
        )

        publish_response = client.post(
            "/publishing/reply",
            json={
                "workspace_id": workspace_id,
                "text": "Founder thread. Practical take with measurable result.",
                "in_reply_to_tweet_id": "190000000000000700",
                "thread_id": "190000000000000700",
                "target_author_id": "100099",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert publish_response.status_code == 200
        payload = publish_response.json()
        assert payload["published"] is True
        assert payload["status"] == "published"
        assert payload["external_post_id"] == "tweet-1"

        with session_factory() as verify_session:
            logs = verify_session.scalars(
                select(PublishAuditLog).where(PublishAuditLog.workspace_id == workspace_id)
            ).all()
            assert len(logs) == 1
            assert logs[0].status == "published"
            assert logs[0].action == "publish_reply"

            usage = verify_session.scalar(
                select(WorkspaceDailyUsage).where(
                    WorkspaceDailyUsage.workspace_id == workspace_id,
                    WorkspaceDailyUsage.action == "publish_reply",
                )
            )
            assert usage is not None
            assert usage.count == 1

            cooldowns = verify_session.scalars(
                select(PublishCooldown).where(PublishCooldown.workspace_id == workspace_id)
            ).all()
            assert len(cooldowns) == 2
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()


def test_publish_reply_is_blocked_by_thread_cooldown(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setenv("PUBLISH_THREAD_COOLDOWN_MINUTES", "120")
    monkeypatch.setenv("PUBLISH_AUTHOR_COOLDOWN_MINUTES", "120")
    get_settings.cache_clear()
    get_token_key.cache_clear()

    session_factory = _build_sqlite_session_factory()
    fake_x = _FakePublisherXClient()

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    api_main.app.dependency_overrides[get_session] = override_get_session
    api_main.app.dependency_overrides[get_x_client] = lambda: fake_x
    try:
        client = TestClient(api_main.app)
        workspace_id, token = _bootstrap_workspace(
            client,
            workspace_name=f"publish-cooldown-{uuid.uuid4()}",
            owner_email="cooldown-owner@revfirst.io",
            owner_password="cooldown-owner-pass-123",
        )

        first = client.post(
            "/publishing/reply",
            json={
                "workspace_id": workspace_id,
                "text": "First reply in thread.",
                "in_reply_to_tweet_id": "190000000000000701",
                "thread_id": "190000000000000701",
                "target_author_id": "200001",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert first.status_code == 200

        second = client.post(
            "/publishing/reply",
            json={
                "workspace_id": workspace_id,
                "text": "Second reply too fast.",
                "in_reply_to_tweet_id": "190000000000000701",
                "thread_id": "190000000000000701",
                "target_author_id": "200001",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert second.status_code == 409
        assert "cooldown" in second.json()["detail"].lower()

        with session_factory() as verify_session:
            logs = verify_session.scalars(
                select(PublishAuditLog).where(PublishAuditLog.workspace_id == workspace_id)
            ).all()
            assert len(logs) == 2
            assert any(log.status == "blocked_cooldown" for log in logs)

            usage = verify_session.scalar(
                select(WorkspaceDailyUsage).where(
                    WorkspaceDailyUsage.workspace_id == workspace_id,
                    WorkspaceDailyUsage.action == "publish_reply",
                )
            )
            assert usage is not None
            assert usage.count == 1
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()


def test_publish_post_blocks_when_plan_limit_exceeded(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    get_settings.cache_clear()
    get_token_key.cache_clear()

    session_factory = _build_sqlite_session_factory()
    fake_x = _FakePublisherXClient()

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    api_main.app.dependency_overrides[get_session] = override_get_session
    api_main.app.dependency_overrides[get_x_client] = lambda: fake_x
    try:
        client = TestClient(api_main.app)
        workspace_id, token = _bootstrap_workspace(
            client,
            workspace_name=f"publish-post-{uuid.uuid4()}",
            owner_email="post-owner@revfirst.io",
            owner_password="post-owner-pass-123",
        )

        first = client.post(
            "/publishing/post",
            json={
                "workspace_id": workspace_id,
                "text": "Daily post 1 for builders.",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert first.status_code == 200

        second = client.post(
            "/publishing/post",
            json={
                "workspace_id": workspace_id,
                "text": "Daily post 2 should hit free-plan limit.",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert second.status_code == 409
        assert "limit" in second.json()["detail"].lower()

        with session_factory() as verify_session:
            logs = verify_session.scalars(
                select(PublishAuditLog).where(PublishAuditLog.workspace_id == workspace_id)
            ).all()
            assert len(logs) == 2
            assert any(log.status == "blocked_plan" for log in logs)

            usage = verify_session.scalar(
                select(WorkspaceDailyUsage).where(
                    WorkspaceDailyUsage.workspace_id == workspace_id,
                    WorkspaceDailyUsage.action == "publish_post",
                )
            )
            assert usage is not None
            assert usage.count == 1
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()
