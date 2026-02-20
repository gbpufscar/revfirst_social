from __future__ import annotations

import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

import src.orchestrator.pipeline as orchestrator_pipeline
from src.core.config import get_settings
from src.orchestrator.pipeline import run_workspace_pipeline
from src.storage.db import Base, load_models
from src.storage.models import IngestionCandidate, Workspace, WorkspaceEvent
from src.storage.security import get_token_key
from src.integrations.x.service import upsert_workspace_x_tokens


class _FakeSearchXClient:
    default_open_calls_query = "builder query"

    def search_open_calls(self, *, access_token: str, query: str | None = None, max_results: int = 20):  # noqa: ARG002
        return {
            "data": [
                {
                    "id": "190000000000100001",
                    "author_id": "5001",
                    "conversation_id": "190000000000100001",
                    "created_at": "2026-02-20T12:00:00Z",
                    "public_metrics": {"like_count": 8, "reply_count": 3, "retweet_count": 1},
                    "lang": "en",
                    "text": "What are you building this week? Share your startup.",
                }
            ],
            "includes": {
                "users": [
                    {
                        "id": "5001",
                        "username": "builder_alpha",
                        "name": "Builder Alpha",
                    }
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


def test_vertical_pipeline_end_to_end_canonical(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setattr(
        orchestrator_pipeline,
        "evaluate_candidate_bundle",
        lambda _: {"brand_consistency": {"passed": True}, "cringe_guard": {"passed": True}},
    )
    get_settings.cache_clear()
    get_token_key.cache_clear()

    session_factory = _build_sqlite_session_factory()
    workspace_id = str(uuid.uuid4())
    fake_x = _FakeSearchXClient()

    try:
        with session_factory() as session:
            session.add(
                Workspace(
                    id=workspace_id,
                    name=f"workspace-{uuid.uuid4()}",
                    plan="free",
                    subscription_status="active",
                )
            )
            session.commit()
            upsert_workspace_x_tokens(
                session,
                workspace_id=workspace_id,
                access_token="workspace-access-token",
                refresh_token="workspace-refresh-token",
                scope="tweet.read users.read",
            )

            result = run_workspace_pipeline(
                session,
                workspace_id=workspace_id,
                x_client=fake_x,
            )
            assert result["status"] == "executed"
            assert result["ingested"] == 1
            assert result["stored_new"] == 1
            assert result["evaluated_candidates"] >= 1

            candidates = session.scalars(
                select(IngestionCandidate).where(IngestionCandidate.workspace_id == workspace_id)
            ).all()
            assert len(candidates) == 1

            events = session.scalars(
                select(WorkspaceEvent).where(WorkspaceEvent.workspace_id == workspace_id)
            ).all()
            assert any(event.event_type == "ingestion_open_calls_run" for event in events)
    finally:
        get_token_key.cache_clear()
        get_settings.cache_clear()
