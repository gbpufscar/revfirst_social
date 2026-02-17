from __future__ import annotations

import uuid
from typing import Optional

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

import src.api.main as api_main
from src.core.config import get_settings
from src.integrations.x.x_client import get_x_client
from src.storage.db import Base, get_session, load_models
from src.storage.models import IngestionCandidate, WorkspaceEvent
from src.storage.security import get_token_key


class _FakeXClient:
    default_open_calls_query = "fake-open-calls-query"

    def search_open_calls(self, *, access_token: str, query: Optional[str] = None, max_results: int = 20):
        del access_token, query, max_results
        return {
            "data": [
                {
                    "id": "190000000000000001",
                    "text": "Drop your SaaS below builders. What are you building?",
                    "author_id": "1001",
                    "conversation_id": "190000000000000001",
                    "lang": "en",
                    "public_metrics": {"like_count": 35, "reply_count": 12, "retweet_count": 6},
                },
                {
                    "id": "190000000000000002",
                    "text": "How did you get your first 10 customers?",
                    "author_id": "1002",
                    "conversation_id": "190000000000000002",
                    "lang": "en",
                    "public_metrics": {"like_count": 12, "reply_count": 3, "retweet_count": 1},
                },
            ],
            "includes": {
                "users": [
                    {"id": "1001", "username": "builder_one"},
                    {"id": "1002", "username": "founder_two"},
                ]
            },
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


def test_open_calls_ingestion_is_read_only_and_workspace_scoped(monkeypatch) -> None:
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
    api_main.app.dependency_overrides[get_x_client] = lambda: _FakeXClient()

    try:
        client = TestClient(api_main.app)
        create_response = client.post(
            "/workspaces",
            json={
                "name": f"ingestion-workspace-{uuid.uuid4()}",
                "owner_email": "ingest-owner@revfirst.io",
                "owner_password": "ingest-secret-123",
            },
        )
        assert create_response.status_code == 201
        workspace_id = create_response.json()["workspace_id"]

        login_response = client.post(
            "/auth/login",
            json={
                "email": "ingest-owner@revfirst.io",
                "password": "ingest-secret-123",
                "workspace_id": workspace_id,
            },
        )
        assert login_response.status_code == 200
        auth_token = login_response.json()["access_token"]

        bootstrap_token = client.post(
            "/integrations/x/oauth/token/manual",
            json={
                "workspace_id": workspace_id,
                "access_token": "workspace-x-access-token",
                "refresh_token": "workspace-x-refresh-token",
                "expires_in": 3600,
                "scope": "tweet.read users.read",
            },
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert bootstrap_token.status_code == 200

        first_run = client.post(
            "/ingestion/open-calls/run",
            json={
                "workspace_id": workspace_id,
                "max_results": 10,
            },
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert first_run.status_code == 200
        first_payload = first_run.json()
        assert first_payload["fetched"] == 2
        assert first_payload["stored_new"] == 2
        assert first_payload["stored_updated"] == 0

        second_run = client.post(
            "/ingestion/open-calls/run",
            json={
                "workspace_id": workspace_id,
                "max_results": 10,
            },
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert second_run.status_code == 200
        second_payload = second_run.json()
        assert second_payload["fetched"] == 2
        assert second_payload["stored_new"] == 0
        assert second_payload["stored_updated"] == 2

        list_response = client.get(
            f"/ingestion/candidates/{workspace_id}?limit=20",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert list_response.status_code == 200
        listed = list_response.json()
        assert listed["count"] == 2
        assert listed["candidates"][0]["opportunity_score"] >= listed["candidates"][1]["opportunity_score"]

        with session_factory() as verify_session:
            stored_candidates = verify_session.scalars(
                select(IngestionCandidate).where(IngestionCandidate.workspace_id == workspace_id)
            ).all()
            assert len(stored_candidates) == 2

            run_events = verify_session.scalars(
                select(WorkspaceEvent).where(
                    WorkspaceEvent.workspace_id == workspace_id,
                    WorkspaceEvent.event_type == "ingestion_open_calls_run",
                )
            ).all()
            assert len(run_events) == 2
    finally:
        api_main.app.dependency_overrides.clear()
        get_token_key.cache_clear()
        get_settings.cache_clear()
