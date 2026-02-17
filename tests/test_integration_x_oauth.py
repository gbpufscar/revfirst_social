from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

import src.api.main as api_main
from src.core.config import get_settings
from src.integrations.x.service import get_workspace_x_access_token
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

        create_response = client.post(
            "/workspaces",
            json={
                "name": f"x-workspace-{uuid.uuid4()}",
                "owner_email": "xowner@revfirst.io",
                "owner_password": "owner-secret-123",
            },
        )
        workspace_id = create_response.json()["workspace_id"]

        login_response = client.post(
            "/auth/login",
            json={
                "email": "xowner@revfirst.io",
                "password": "owner-secret-123",
                "workspace_id": workspace_id,
            },
        )
        token = login_response.json()["access_token"]

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
        assert manual_token_response.json()["connected"] is True

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
