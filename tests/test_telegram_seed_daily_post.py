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
from src.storage.models import DailyPostDraft, TelegramSeed, WorkspaceDailyUsage
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
    return workspace_id, token


def test_phase9_webhook_seed_and_generate_ready_post(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "phase9-secret")
    get_settings.cache_clear()
    get_token_key.cache_clear()

    session_factory = _build_sqlite_session_factory()

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    api_main.app.dependency_overrides[get_session] = override_get_session
    try:
        client = TestClient(api_main.app)
        workspace_id, token = _bootstrap_workspace(
            client,
            workspace_name=f"phase9-seed-{uuid.uuid4()}",
            owner_email="phase9-seed-owner@revfirst.io",
            owner_password="phase9-seed-owner-pass",
        )

        webhook = client.post(
            f"/integrations/telegram/webhook/{workspace_id}",
            json={
                "message": {
                    "message_id": 7001,
                    "chat": {"id": 99001},
                    "from": {"id": 44001},
                    "text": "Founder pipeline note: builders can move revenue with weekly tests and direct replies.",
                }
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase9-secret"},
        )
        assert webhook.status_code == 200
        assert webhook.json()["accepted"] is True

        generate = client.post(
            "/daily-post/generate",
            json={
                "workspace_id": workspace_id,
                "topic": "reply pipeline",
                "auto_publish": False,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert generate.status_code == 200
        payload = generate.json()
        assert payload["status"] == "ready"
        assert payload["brand_passed"] is True
        assert payload["cringe_passed"] is True
        assert payload["published"] is False
        assert payload["seed_count"] == 1

        with session_factory() as verify_session:
            seeds = verify_session.scalars(select(TelegramSeed).where(TelegramSeed.workspace_id == workspace_id)).all()
            assert len(seeds) == 1
            drafts = verify_session.scalars(
                select(DailyPostDraft).where(DailyPostDraft.workspace_id == workspace_id)
            ).all()
            assert len(drafts) == 1
            assert drafts[0].status == "ready"
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()


def test_phase9_generate_with_auto_publish_creates_post_usage(monkeypatch) -> None:
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
            workspace_name=f"phase9-publish-{uuid.uuid4()}",
            owner_email="phase9-publish-owner@revfirst.io",
            owner_password="phase9-publish-owner-pass",
        )

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

        manual_seed = client.post(
            "/integrations/telegram/seed/manual",
            json={
                "workspace_id": workspace_id,
                "text": "Founders can improve conversion by answering builder replies with direct revenue context.",
                "source_chat_id": "manual-chat",
                "source_message_id": "manual-message-1",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert manual_seed.status_code == 200

        generate = client.post(
            "/daily-post/generate",
            json={
                "workspace_id": workspace_id,
                "topic": "conversion notes",
                "auto_publish": True,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert generate.status_code == 200
        payload = generate.json()
        assert payload["published"] is True
        assert payload["status"] == "published"
        assert payload["external_post_id"] == "tweet-1"

        with session_factory() as verify_session:
            usage = verify_session.scalar(
                select(WorkspaceDailyUsage).where(
                    WorkspaceDailyUsage.workspace_id == workspace_id,
                    WorkspaceDailyUsage.action == "publish_post",
                )
            )
            assert usage is not None
            assert usage.count == 1
            drafts = verify_session.scalars(
                select(DailyPostDraft).where(DailyPostDraft.workspace_id == workspace_id)
            ).all()
            assert len(drafts) == 1
            assert drafts[0].status == "published"
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()


def test_phase9_webhook_rejects_invalid_secret(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "phase9-secret")
    get_settings.cache_clear()
    get_token_key.cache_clear()

    session_factory = _build_sqlite_session_factory()

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    api_main.app.dependency_overrides[get_session] = override_get_session
    try:
        client = TestClient(api_main.app)
        workspace_id, _ = _bootstrap_workspace(
            client,
            workspace_name=f"phase9-secret-{uuid.uuid4()}",
            owner_email="phase9-secret-owner@revfirst.io",
            owner_password="phase9-secret-owner-pass",
        )

        webhook = client.post(
            f"/integrations/telegram/webhook/{workspace_id}",
            json={
                "message": {
                    "message_id": 7002,
                    "chat": {"id": 99002},
                    "from": {"id": 44002},
                    "text": "test",
                }
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
        )
        assert webhook.status_code == 401
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()

