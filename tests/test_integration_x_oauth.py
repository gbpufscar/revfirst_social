from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

import src.api.main as api_main
import src.integrations.x.service as x_service
from src.core.config import get_settings
from src.integrations.x.service import get_workspace_x_access_token
from src.integrations.x.x_client import get_x_client
from src.storage.db import Base, get_session, load_models
from src.storage.models import XOAuthToken
from src.storage.security import get_token_key, hash_token


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


def _create_workspace_and_login(client: TestClient, *, email: str) -> tuple[str, str]:
    owner_password = "owner-secret-123"
    create_response = client.post(
        "/workspaces",
        json={
            "name": f"x-workspace-{uuid.uuid4()}",
            "owner_email": email,
            "owner_password": owner_password,
        },
    )
    workspace_id = create_response.json()["workspace_id"]

    login_response = client.post(
        "/auth/login",
        json={
            "email": email,
            "password": owner_password,
            "workspace_id": workspace_id,
        },
    )
    token = login_response.json()["access_token"]
    return workspace_id, token


def _bootstrap_workspace(client: TestClient, *, email: str) -> tuple[str, str]:
    workspace_id, token = _create_workspace_and_login(client, email=email)

    manual_token_response = client.post(
        "/integrations/x/oauth/token/manual",
        json={
            "workspace_id": workspace_id,
            "access_token": "x-access-token-secret",
            "refresh_token": "x-refresh-token-secret",
            "expires_in": 3600,
            "scope": "tweet.read users.read",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert manual_token_response.status_code == 200
    return workspace_id, token


class _FakeRefreshingXClient:
    def __init__(self, *, payload: dict[str, object] | None = None, raise_error: bool = False) -> None:
        self.payload = payload or {}
        self.raise_error = raise_error
        self.calls: list[str] = []

    def refresh_access_token(self, *, refresh_token: str) -> dict[str, object]:
        self.calls.append(refresh_token)
        if self.raise_error:
            raise RuntimeError("refresh_failed")
        return dict(self.payload)


class _FakeRedisLockClient:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):  # noqa: ARG002
        if nx and key in self._store:
            return False
        self._store[key] = value
        return True

    def eval(self, script: str, num_keys: int, *args):  # noqa: ARG002
        if len(args) == 2:
            key, token = args
            if self._store.get(key) == token:
                del self._store[key]
                return 1
            return 0
        if len(args) == 1:
            key = args[0]
            value = self._store.get(key)
            if value is None:
                return None
            del self._store[key]
            return value
        return 0


class _FakeOAuthFlowXClient:
    def __init__(
        self,
        *,
        scope: str = "tweet.read tweet.write users.read offline.access",
        user_id: str = "12345",
        username: str = "revfirst_official",
    ) -> None:
        self.scope = scope
        self.user_id = user_id
        self.username = username
        self.exchange_calls: list[tuple[str, str | None]] = []

    def exchange_code_for_tokens(
        self,
        *,
        authorization_code: str,
        code_verifier: str | None = None,
    ) -> dict[str, object]:
        self.exchange_calls.append((authorization_code, code_verifier))
        return {
            "access_token": "oauth-access-token",
            "refresh_token": "oauth-refresh-token",
            "token_type": "bearer",
            "scope": self.scope,
            "expires_in": 3600,
        }

    def get_authenticated_user(self, *, access_token: str) -> dict[str, object]:
        assert access_token == "oauth-access-token"
        return {"id": self.user_id, "username": self.username}


def test_x_manual_token_is_stored_hashed_and_encrypted(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
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
        workspace_id, token = _bootstrap_workspace(client, email="xowner@revfirst.io")
        assert token

        with session_factory() as verify_session:
            row = verify_session.scalar(
                select(XOAuthToken).where(XOAuthToken.workspace_id == workspace_id)
            )
            assert row is not None
            assert row.access_token_hash == hash_token("x-access-token-secret")
            assert row.access_token_encrypted != "x-access-token-secret"
            assert row.refresh_token_hash == hash_token("x-refresh-token-secret")
            assert row.refresh_token_encrypted != "x-refresh-token-secret"

            decrypted_access_token = get_workspace_x_access_token(
                verify_session,
                workspace_id=workspace_id,
            )
            assert decrypted_access_token == "x-access-token-secret"
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()


def test_get_workspace_x_access_token_refreshes_expired_token(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setenv("X_AUTO_REFRESH_ENABLED", "true")
    monkeypatch.setenv("X_REFRESH_SKEW_SECONDS", "300")
    monkeypatch.setenv("X_REFRESH_LOCK_TTL_SECONDS", "30")
    get_settings.cache_clear()
    get_token_key.cache_clear()

    session_factory = _build_sqlite_session_factory()
    fake_x_client = _FakeRefreshingXClient(
        payload={
            "access_token": "x-access-token-rotated",
            "refresh_token": "x-refresh-token-rotated",
            "token_type": "bearer",
            "scope": "tweet.read tweet.write users.read",
            "expires_in": 7200,
        }
    )
    fake_redis = _FakeRedisLockClient()
    monkeypatch.setattr(x_service, "get_x_client", lambda: fake_x_client)
    monkeypatch.setattr(x_service, "get_redis_client", lambda: fake_redis)

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    api_main.app.dependency_overrides[get_session] = override_get_session
    try:
        client = TestClient(api_main.app)
        workspace_id, token = _bootstrap_workspace(client, email="xrefresh@revfirst.io")
        assert token

        with session_factory() as update_session:
            row = update_session.scalar(select(XOAuthToken).where(XOAuthToken.workspace_id == workspace_id))
            assert row is not None
            row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
            update_session.commit()

        with session_factory() as verify_session:
            refreshed_access_token = get_workspace_x_access_token(
                verify_session,
                workspace_id=workspace_id,
            )
            assert refreshed_access_token == "x-access-token-rotated"

            updated_row = verify_session.scalar(
                select(XOAuthToken).where(XOAuthToken.workspace_id == workspace_id)
            )
            assert updated_row is not None
            assert updated_row.access_token_hash == hash_token("x-access-token-rotated")
            assert updated_row.refresh_token_hash == hash_token("x-refresh-token-rotated")
            assert updated_row.expires_at is not None
            expires_at = updated_row.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            assert expires_at > datetime.now(timezone.utc)

        assert fake_x_client.calls == ["x-refresh-token-secret"]
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()


def test_get_workspace_x_access_token_falls_back_when_refresh_fails_but_token_still_valid(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setenv("X_AUTO_REFRESH_ENABLED", "true")
    monkeypatch.setenv("X_REFRESH_SKEW_SECONDS", "3600")
    monkeypatch.setenv("X_REFRESH_LOCK_TTL_SECONDS", "30")
    get_settings.cache_clear()
    get_token_key.cache_clear()

    session_factory = _build_sqlite_session_factory()
    fake_x_client = _FakeRefreshingXClient(raise_error=True)
    fake_redis = _FakeRedisLockClient()
    monkeypatch.setattr(x_service, "get_x_client", lambda: fake_x_client)
    monkeypatch.setattr(x_service, "get_redis_client", lambda: fake_redis)

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    api_main.app.dependency_overrides[get_session] = override_get_session
    try:
        client = TestClient(api_main.app)
        workspace_id, token = _bootstrap_workspace(client, email="xrefresh-fallback@revfirst.io")
        assert token

        with session_factory() as update_session:
            row = update_session.scalar(select(XOAuthToken).where(XOAuthToken.workspace_id == workspace_id))
            assert row is not None
            row.expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
            update_session.commit()

        with session_factory() as verify_session:
            access_token = get_workspace_x_access_token(
                verify_session,
                workspace_id=workspace_id,
            )
            assert access_token == "x-access-token-secret"

        assert fake_x_client.calls == ["x-refresh-token-secret"]
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()


def test_get_workspace_x_access_token_returns_none_when_refresh_fails_and_token_is_expired(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setenv("X_AUTO_REFRESH_ENABLED", "true")
    monkeypatch.setenv("X_REFRESH_SKEW_SECONDS", "300")
    monkeypatch.setenv("X_REFRESH_LOCK_TTL_SECONDS", "30")
    get_settings.cache_clear()
    get_token_key.cache_clear()

    session_factory = _build_sqlite_session_factory()
    fake_x_client = _FakeRefreshingXClient(raise_error=True)
    fake_redis = _FakeRedisLockClient()
    monkeypatch.setattr(x_service, "get_x_client", lambda: fake_x_client)
    monkeypatch.setattr(x_service, "get_redis_client", lambda: fake_redis)

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    api_main.app.dependency_overrides[get_session] = override_get_session
    try:
        client = TestClient(api_main.app)
        workspace_id, token = _bootstrap_workspace(client, email="xrefresh-expired@revfirst.io")
        assert token

        with session_factory() as update_session:
            row = update_session.scalar(select(XOAuthToken).where(XOAuthToken.workspace_id == workspace_id))
            assert row is not None
            row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            update_session.commit()

        with session_factory() as verify_session:
            access_token = get_workspace_x_access_token(
                verify_session,
                workspace_id=workspace_id,
            )
            assert access_token is None

        assert fake_x_client.calls == ["x-refresh-token-secret"]
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()


def test_oauth_status_expired_without_refresh_reports_disconnected(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setenv("X_AUTO_REFRESH_ENABLED", "true")
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
        workspace_id, token = _bootstrap_workspace(client, email="xstatus-norefresh@revfirst.io")

        without_refresh = client.post(
            "/integrations/x/oauth/token/manual",
            json={
                "workspace_id": workspace_id,
                "access_token": "x-access-token-no-refresh",
                "expires_in": 3600,
                "scope": "tweet.read users.read",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert without_refresh.status_code == 200

        with session_factory() as update_session:
            row = update_session.scalar(select(XOAuthToken).where(XOAuthToken.workspace_id == workspace_id))
            assert row is not None
            row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            update_session.commit()

        status_response = client.get(
            f"/integrations/x/oauth/status/{workspace_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert status_response.status_code == 200
        payload = status_response.json()
        assert payload["connected"] is False
        assert payload["connected_reason"] == "access_token_expired"
        assert payload["access_token_valid"] is False
        assert payload["is_expired"] is True
        assert payload["has_refresh_token"] is False
        assert payload["can_auto_refresh"] is False
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()


def test_oauth_status_expired_with_refresh_reports_connected_via_auto_refresh(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setenv("X_AUTO_REFRESH_ENABLED", "true")
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
        workspace_id, token = _bootstrap_workspace(client, email="xstatus-refresh@revfirst.io")

        with session_factory() as update_session:
            row = update_session.scalar(select(XOAuthToken).where(XOAuthToken.workspace_id == workspace_id))
            assert row is not None
            row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            update_session.commit()

        status_response = client.get(
            f"/integrations/x/oauth/status/{workspace_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert status_response.status_code == 200
        payload = status_response.json()
        assert payload["connected"] is True
        assert payload["connected_reason"] == "expired_but_auto_refresh_available"
        assert payload["access_token_valid"] is False
        assert payload["is_expired"] is True
        assert payload["has_refresh_token"] is True
        assert payload["can_auto_refresh"] is True
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()


def test_oauth_status_revoked_reports_disconnected(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setenv("X_AUTO_REFRESH_ENABLED", "true")
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
        workspace_id, token = _bootstrap_workspace(client, email="xstatus-revoked@revfirst.io")

        revoke_response = client.post(
            f"/integrations/x/oauth/revoke/{workspace_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert revoke_response.status_code == 200
        assert revoke_response.json()["revoked"] is True

        status_response = client.get(
            f"/integrations/x/oauth/status/{workspace_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert status_response.status_code == 200
        payload = status_response.json()
        assert payload["connected"] is False
        assert payload["connected_reason"] == "revoked"
        assert payload["can_auto_refresh"] is False
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()


def test_oauth_authorize_and_callback_persists_account_identity(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setenv("X_CLIENT_ID", "x-client-id")
    monkeypatch.setenv("X_CLIENT_SECRET", "x-client-secret")
    monkeypatch.setenv("X_REDIRECT_URI", "https://social.revfirst.cloud/integrations/x/oauth/callback")
    monkeypatch.setenv("X_OAUTH_DEFAULT_SCOPES", "tweet.read tweet.write users.read offline.access")
    monkeypatch.setenv("X_REQUIRED_PUBLISH_SCOPE", "tweet.write")
    get_settings.cache_clear()
    get_token_key.cache_clear()

    session_factory = _build_sqlite_session_factory()
    fake_redis = _FakeRedisLockClient()
    fake_x_client = _FakeOAuthFlowXClient()
    monkeypatch.setattr(x_service, "get_redis_client", lambda: fake_redis)

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    api_main.app.dependency_overrides[get_session] = override_get_session
    api_main.app.dependency_overrides[get_x_client] = lambda: fake_x_client
    try:
        client = TestClient(api_main.app)
        workspace_id, token = _create_workspace_and_login(client, email="xoauth-callback@revfirst.io")

        authorize_response = client.post(
            "/integrations/x/oauth/authorize",
            json={"workspace_id": workspace_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert authorize_response.status_code == 200
        authorize_payload = authorize_response.json()
        state = authorize_payload["state"]
        assert "code_challenge_method=S256" in authorize_payload["authorize_url"]

        callback_response = client.get(
            "/integrations/x/oauth/callback",
            params={"code": "auth-code-1", "state": state},
        )
        assert callback_response.status_code == 200
        callback_payload = callback_response.json()
        assert callback_payload["workspace_id"] == workspace_id
        assert callback_payload["connected"] is True
        assert callback_payload["has_publish_scope"] is True
        assert callback_payload["account_user_id"] == "12345"
        assert callback_payload["account_username"] == "revfirst_official"

        with session_factory() as verify_session:
            row = verify_session.scalar(select(XOAuthToken).where(XOAuthToken.workspace_id == workspace_id))
            assert row is not None
            assert row.account_user_id == "12345"
            assert row.account_username == "revfirst_official"

        assert fake_x_client.exchange_calls
        assert fake_x_client.exchange_calls[0][0] == "auth-code-1"
        assert isinstance(fake_x_client.exchange_calls[0][1], str)
        assert fake_x_client.exchange_calls[0][1]
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()


def test_oauth_callback_rejects_state_replay(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setenv("X_CLIENT_ID", "x-client-id")
    monkeypatch.setenv("X_CLIENT_SECRET", "x-client-secret")
    monkeypatch.setenv("X_REDIRECT_URI", "https://social.revfirst.cloud/integrations/x/oauth/callback")
    monkeypatch.setenv("X_OAUTH_DEFAULT_SCOPES", "tweet.read tweet.write users.read offline.access")
    monkeypatch.setenv("X_REQUIRED_PUBLISH_SCOPE", "tweet.write")
    get_settings.cache_clear()
    get_token_key.cache_clear()

    session_factory = _build_sqlite_session_factory()
    fake_redis = _FakeRedisLockClient()
    fake_x_client = _FakeOAuthFlowXClient()
    monkeypatch.setattr(x_service, "get_redis_client", lambda: fake_redis)

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    api_main.app.dependency_overrides[get_session] = override_get_session
    api_main.app.dependency_overrides[get_x_client] = lambda: fake_x_client
    try:
        client = TestClient(api_main.app)
        workspace_id, token = _create_workspace_and_login(client, email="xoauth-replay@revfirst.io")

        authorize_response = client.post(
            "/integrations/x/oauth/authorize",
            json={"workspace_id": workspace_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert authorize_response.status_code == 200
        state = authorize_response.json()["state"]

        first_callback = client.get(
            "/integrations/x/oauth/callback",
            params={"code": "auth-code-2", "state": state},
        )
        assert first_callback.status_code == 200

        replay_callback = client.get(
            "/integrations/x/oauth/callback",
            params={"code": "auth-code-3", "state": state},
        )
        assert replay_callback.status_code == 400
        assert "state" in replay_callback.json()["detail"].lower()
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()


def test_oauth_exchange_requires_publish_scope(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setenv("X_REQUIRED_PUBLISH_SCOPE", "tweet.write")
    get_settings.cache_clear()
    get_token_key.cache_clear()

    session_factory = _build_sqlite_session_factory()
    fake_x_client = _FakeOAuthFlowXClient(scope="tweet.read users.read")

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    api_main.app.dependency_overrides[get_session] = override_get_session
    api_main.app.dependency_overrides[get_x_client] = lambda: fake_x_client
    try:
        client = TestClient(api_main.app)
        workspace_id, token = _create_workspace_and_login(client, email="xoauth-exchange-missing-scope@revfirst.io")

        exchange_response = client.post(
            "/integrations/x/oauth/exchange",
            json={
                "workspace_id": workspace_id,
                "authorization_code": "exchange-code-1",
                "code_verifier": "a" * 48,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert exchange_response.status_code == 400
        assert "scope" in exchange_response.json()["detail"].lower()
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()


def test_oauth_exchange_success_persists_account_identity(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setenv("X_REQUIRED_PUBLISH_SCOPE", "tweet.write")
    get_settings.cache_clear()
    get_token_key.cache_clear()

    session_factory = _build_sqlite_session_factory()
    fake_x_client = _FakeOAuthFlowXClient(scope="tweet.read tweet.write users.read offline.access")

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    api_main.app.dependency_overrides[get_session] = override_get_session
    api_main.app.dependency_overrides[get_x_client] = lambda: fake_x_client
    try:
        client = TestClient(api_main.app)
        workspace_id, token = _create_workspace_and_login(client, email="xoauth-exchange-success@revfirst.io")

        exchange_response = client.post(
            "/integrations/x/oauth/exchange",
            json={
                "workspace_id": workspace_id,
                "authorization_code": "exchange-code-2",
                "code_verifier": "b" * 48,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert exchange_response.status_code == 200
        payload = exchange_response.json()
        assert payload["connected"] is True
        assert payload["has_publish_scope"] is True
        assert payload["account_user_id"] == "12345"
        assert payload["account_username"] == "revfirst_official"

        with session_factory() as verify_session:
            row = verify_session.scalar(select(XOAuthToken).where(XOAuthToken.workspace_id == workspace_id))
            assert row is not None
            assert row.account_user_id == "12345"
            assert row.account_username == "revfirst_official"
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()
