from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

import src.api.main as api_main
from src.core.config import get_settings
from src.core.metrics import reset_metrics_for_tests
import src.publishing.service as publishing_service
from src.integrations.x.x_client import XClientError, get_x_client
from src.storage.db import Base, get_session, load_models
from src.storage.models import (
    PublishAuditLog,
    PublishCooldown,
    Role,
    User,
    WorkspaceControlSetting,
    WorkspaceDailyUsage,
    WorkspaceUser,
)
from src.storage.security import get_token_key, hash_password


class _FakePublisherXClient:
    def __init__(self) -> None:
        self.counter = 0

    def create_tweet(self, *, access_token: str, text: str, in_reply_to_tweet_id: str | None = None):
        del access_token, text, in_reply_to_tweet_id
        self.counter += 1
        return {"data": {"id": f"tweet-{self.counter}"}}


class _FailingPublisherXClient:
    def create_tweet(self, *, access_token: str, text: str, in_reply_to_tweet_id: str | None = None):
        del access_token, text, in_reply_to_tweet_id
        raise XClientError("forced_publish_failure")


class _FakeCounterRedis:
    def __init__(self) -> None:
        self._store: dict[str, int] = {}

    def get(self, key: str):
        value = self._store.get(key)
        return None if value is None else str(value)

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        del ex
        if nx and key in self._store:
            return False
        self._store[key] = int(value) if str(value).isdigit() else 0
        return True

    def delete(self, key: str):
        return 1 if self._store.pop(key, None) is not None else 0

    def incr(self, key: str):
        value = int(self._store.get(key, 0)) + 1
        self._store[key] = value
        return value

    def expire(self, key: str, seconds: int):
        del key, seconds
        return True


def _publish_headers(token: str, internal_key: str = "test-internal-publish-key") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-RevFirst-Internal-Key": internal_key,
    }


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
    monkeypatch.setenv("PUBLISHING_DIRECT_API_ENABLED", "true")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_INTERNAL_KEY", "test-internal-publish-key")
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
            headers=_publish_headers(token),
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
    monkeypatch.setenv("PUBLISHING_DIRECT_API_ENABLED", "true")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_INTERNAL_KEY", "test-internal-publish-key")
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
            headers=_publish_headers(token),
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
            headers=_publish_headers(token),
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


def test_publish_reply_is_blocked_by_hourly_quota(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setenv("PUBLISH_THREAD_COOLDOWN_MINUTES", "1")
    monkeypatch.setenv("PUBLISH_AUTHOR_COOLDOWN_MINUTES", "1")
    monkeypatch.setenv("MAX_REPLIES_PER_HOUR", "1")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_ENABLED", "true")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_INTERNAL_KEY", "test-internal-publish-key")
    get_settings.cache_clear()
    get_token_key.cache_clear()

    session_factory = _build_sqlite_session_factory()
    fake_x = _FakePublisherXClient()
    fake_redis = _FakeCounterRedis()
    monkeypatch.setattr(publishing_service, "get_redis_client", lambda: fake_redis)

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
            workspace_name=f"publish-hourly-quota-{uuid.uuid4()}",
            owner_email="hourly-quota-owner@revfirst.io",
            owner_password="hourly-quota-owner-pass-123",
        )

        first = client.post(
            "/publishing/reply",
            json={
                "workspace_id": workspace_id,
                "text": "First reply should publish.",
                "in_reply_to_tweet_id": "190000000000001001",
                "thread_id": "190000000000001001",
                "target_author_id": "900001",
            },
            headers=_publish_headers(token),
        )
        assert first.status_code == 200

        second = client.post(
            "/publishing/reply",
            json={
                "workspace_id": workspace_id,
                "text": "Second reply in same hour should be blocked by quota.",
                "in_reply_to_tweet_id": "190000000000001002",
                "thread_id": "190000000000001002",
                "target_author_id": "900002",
            },
            headers=_publish_headers(token),
        )
        assert second.status_code == 409
        assert "quota" in second.json()["detail"].lower()

        with session_factory() as verify_session:
            logs = verify_session.scalars(
                select(PublishAuditLog).where(PublishAuditLog.workspace_id == workspace_id)
            ).all()
            assert len(logs) == 2
            assert any(log.status == "blocked_rate_limit" for log in logs)
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()


def test_publish_post_consecutive_failures_trigger_circuit_breaker(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setenv("MAX_CONSECUTIVE_PUBLISH_FAILURES", "2")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_ENABLED", "true")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_INTERNAL_KEY", "test-internal-publish-key")
    get_settings.cache_clear()
    get_token_key.cache_clear()

    session_factory = _build_sqlite_session_factory()
    fake_x = _FailingPublisherXClient()
    fake_redis = _FakeCounterRedis()
    monkeypatch.setattr(publishing_service, "get_redis_client", lambda: fake_redis)

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
            workspace_name=f"publish-breaker-{uuid.uuid4()}",
            owner_email="publish-breaker-owner@revfirst.io",
            owner_password="publish-breaker-owner-pass-123",
        )

        first = client.post(
            "/publishing/post",
            json={
                "workspace_id": workspace_id,
                "text": "Attempt 1 must fail.",
            },
            headers=_publish_headers(token),
        )
        assert first.status_code == 502

        second = client.post(
            "/publishing/post",
            json={
                "workspace_id": workspace_id,
                "text": "Attempt 2 must fail and trigger breaker.",
            },
            headers=_publish_headers(token),
        )
        assert second.status_code == 502

        third = client.post(
            "/publishing/post",
            json={
                "workspace_id": workspace_id,
                "text": "Attempt 3 must be blocked by breaker/containment.",
            },
            headers=_publish_headers(token),
        )
        assert third.status_code == 409
        detail = third.json()["detail"].lower()
        assert "operational mode" in detail or "circuit breaker" in detail

        with session_factory() as verify_session:
            control_setting = verify_session.scalar(
                select(WorkspaceControlSetting).where(WorkspaceControlSetting.workspace_id == workspace_id)
            )
            assert control_setting is not None
            assert control_setting.operational_mode == "containment"
            assert control_setting.is_paused is True

            logs = verify_session.scalars(
                select(PublishAuditLog).where(PublishAuditLog.workspace_id == workspace_id)
            ).all()
            assert len(logs) >= 3
            assert any(log.status == "failed" for log in logs)
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()


def test_publish_post_blocks_when_plan_limit_exceeded(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_ENABLED", "true")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_INTERNAL_KEY", "test-internal-publish-key")
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
            headers=_publish_headers(token),
        )
        assert first.status_code == 200

        second = client.post(
            "/publishing/post",
            json={
                "workspace_id": workspace_id,
                "text": "Daily post 2 should hit free-plan limit.",
            },
            headers=_publish_headers(token),
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


def test_publish_post_is_blocked_when_direct_api_disabled(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_ENABLED", "false")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_INTERNAL_KEY", "test-internal-publish-key")
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
            workspace_name=f"publish-disabled-{uuid.uuid4()}",
            owner_email="publish-disabled-owner@revfirst.io",
            owner_password="publish-disabled-pass-123",
        )

        response = client.post(
            "/publishing/post",
            json={
                "workspace_id": workspace_id,
                "text": "Should be blocked when direct API disabled.",
            },
            headers=_publish_headers(token),
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "direct_publish_api_disabled"
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()


def test_publish_post_is_blocked_when_internal_key_missing(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_ENABLED", "true")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_INTERNAL_KEY", "test-internal-publish-key")
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
            workspace_name=f"publish-missing-key-{uuid.uuid4()}",
            owner_email="publish-missing-key-owner@revfirst.io",
            owner_password="publish-missing-key-pass-123",
        )

        response = client.post(
            "/publishing/post",
            json={
                "workspace_id": workspace_id,
                "text": "Should be blocked without internal key.",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "invalid_internal_publish_key"
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()


def test_publish_post_is_blocked_for_member_role(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_ENABLED", "true")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_INTERNAL_KEY", "test-internal-publish-key")
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
        workspace_id, owner_token = _bootstrap_workspace(
            client,
            workspace_name=f"publish-member-{uuid.uuid4()}",
            owner_email="publish-member-owner@revfirst.io",
            owner_password="publish-member-pass-123",
        )
        assert owner_token

        member_email = "workspace-member@revfirst.io"
        member_password = "workspace-member-pass-123"
        with session_factory() as seed_session:
            member_role = seed_session.scalar(select(Role).where(Role.name == "member"))
            assert member_role is not None

            member = User(
                id=str(uuid.uuid4()),
                email=member_email,
                password_hash=hash_password(member_password),
                is_active=True,
            )
            seed_session.add(member)
            seed_session.flush()
            seed_session.add(
                WorkspaceUser(
                    id=str(uuid.uuid4()),
                    workspace_id=workspace_id,
                    user_id=member.id,
                    role_id=member_role.id,
                )
            )
            seed_session.commit()

        member_login = client.post(
            "/auth/login",
            json={
                "email": member_email,
                "password": member_password,
                "workspace_id": workspace_id,
            },
        )
        assert member_login.status_code == 200
        member_token = member_login.json()["access_token"]

        response = client.post(
            "/publishing/post",
            json={
                "workspace_id": workspace_id,
                "text": "Member role should not publish directly.",
            },
            headers=_publish_headers(member_token),
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Insufficient role"
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()


def test_publish_post_is_blocked_when_operational_mode_is_containment(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_ENABLED", "true")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_INTERNAL_KEY", "test-internal-publish-key")
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
            workspace_name=f"publish-containment-{uuid.uuid4()}",
            owner_email="publish-containment-owner@revfirst.io",
            owner_password="publish-containment-pass-123",
        )

        with session_factory() as update_session:
            row = update_session.scalar(
                select(WorkspaceControlSetting).where(WorkspaceControlSetting.workspace_id == workspace_id)
            )
            if row is None:
                row = WorkspaceControlSetting(
                    workspace_id=workspace_id,
                    is_paused=False,
                    operational_mode="semi_autonomous",
                    channels_json='{"blog":false,"email":false,"instagram":false,"x":true}',
                )
                update_session.add(row)
                update_session.flush()
            row.operational_mode = "containment"
            update_session.commit()

        response = client.post(
            "/publishing/post",
            json={
                "workspace_id": workspace_id,
                "text": "Containment mode must block publishing.",
            },
            headers=_publish_headers(token),
        )
        assert response.status_code == 409
        assert "operational mode" in response.json()["detail"].lower()
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()


def test_publish_reply_is_blocked_when_direct_api_disabled(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_ENABLED", "false")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_INTERNAL_KEY", "test-internal-publish-key")
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
            workspace_name=f"reply-disabled-{uuid.uuid4()}",
            owner_email="reply-disabled-owner@revfirst.io",
            owner_password="reply-disabled-pass-123",
        )

        response = client.post(
            "/publishing/reply",
            json={
                "workspace_id": workspace_id,
                "text": "Should be blocked when direct API disabled.",
                "in_reply_to_tweet_id": "190000000000000811",
                "thread_id": "190000000000000811",
                "target_author_id": "555001",
            },
            headers=_publish_headers(token),
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "direct_publish_api_disabled"
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()


def test_publish_reply_is_blocked_when_internal_key_missing(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_ENABLED", "true")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_INTERNAL_KEY", "test-internal-publish-key")
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
            workspace_name=f"reply-missing-key-{uuid.uuid4()}",
            owner_email="reply-missing-key-owner@revfirst.io",
            owner_password="reply-missing-key-pass-123",
        )

        response = client.post(
            "/publishing/reply",
            json={
                "workspace_id": workspace_id,
                "text": "Should be blocked without internal key.",
                "in_reply_to_tweet_id": "190000000000000812",
                "thread_id": "190000000000000812",
                "target_author_id": "555002",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "invalid_internal_publish_key"
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()


def test_publish_reply_success_increments_replies_published_metric_once(monkeypatch) -> None:
    reset_metrics_for_tests()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_ENABLED", "true")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_INTERNAL_KEY", "test-internal-publish-key")
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
            workspace_name=f"reply-metric-success-{uuid.uuid4()}",
            owner_email="reply-metric-success-owner@revfirst.io",
            owner_password="reply-metric-success-pass-123",
        )

        response = client.post(
            "/publishing/reply",
            json={
                "workspace_id": workspace_id,
                "text": "Successful reply should increment metric once.",
                "in_reply_to_tweet_id": "190000000000000821",
                "thread_id": "190000000000000821",
                "target_author_id": "556001",
            },
            headers=_publish_headers(token),
        )
        assert response.status_code == 200

        metrics_response = client.get("/metrics")
        assert metrics_response.status_code == 200
        assert (
            f'revfirst_replies_published_total{{workspace_id="{workspace_id}"}} 1'
            in metrics_response.text
        )
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()
        reset_metrics_for_tests()


def test_publish_reply_author_cooldown_does_not_increment_replies_published_metric(monkeypatch) -> None:
    reset_metrics_for_tests()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setenv("PUBLISH_THREAD_COOLDOWN_MINUTES", "120")
    monkeypatch.setenv("PUBLISH_AUTHOR_COOLDOWN_MINUTES", "120")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_ENABLED", "true")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_INTERNAL_KEY", "test-internal-publish-key")
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
            workspace_name=f"reply-metric-author-cooldown-{uuid.uuid4()}",
            owner_email="reply-metric-author-cooldown-owner@revfirst.io",
            owner_password="reply-metric-author-cooldown-pass-123",
        )

        first = client.post(
            "/publishing/reply",
            json={
                "workspace_id": workspace_id,
                "text": "First reply should publish.",
                "in_reply_to_tweet_id": "190000000000000831",
                "thread_id": "190000000000000831",
                "target_author_id": "556101",
            },
            headers=_publish_headers(token),
        )
        assert first.status_code == 200

        second = client.post(
            "/publishing/reply",
            json={
                "workspace_id": workspace_id,
                "text": "Second reply same author should be blocked.",
                "in_reply_to_tweet_id": "190000000000000832",
                "thread_id": "190000000000000832",
                "target_author_id": "556101",
            },
            headers=_publish_headers(token),
        )
        assert second.status_code == 409
        assert "cooldown" in second.json()["detail"].lower()

        metrics_response = client.get("/metrics")
        assert metrics_response.status_code == 200
        assert (
            f'revfirst_replies_published_total{{workspace_id="{workspace_id}"}} 1'
            in metrics_response.text
        )
        assert (
            f'revfirst_reply_blocked_total{{workspace_id="{workspace_id}",reason="author_cooldown"}} 1'
            in metrics_response.text
        )
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()
        reset_metrics_for_tests()
