from __future__ import annotations

import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.core.config import get_settings
from src.integrations.x.service import upsert_workspace_x_tokens
from src.storage.db import Base, load_models
from src.storage.models import User, Workspace, XStrategyDiscoveryCandidate, XStrategyWatchlist
from src.storage.security import get_token_key
from src.strategy.x_growth_strategy_agent import (
    approve_strategy_candidate,
    list_pending_strategy_candidates,
    run_workspace_strategy_discovery,
)


class _FakeStrategyDiscoveryXClient:
    def search_open_calls(self, *, access_token: str, query: str | None = None, max_results: int = 20):  # noqa: ARG002
        return {
            "data": [
                {"id": "t1", "author_id": "1001", "text": "building in public", "created_at": "2026-02-21T00:00:00Z"},
                {"id": "t2", "author_id": "1001", "text": "just launched", "created_at": "2026-02-21T06:00:00Z"},
            ],
            "includes": {
                "users": [
                    {"id": "1001", "username": "Tobby_scraper", "name": "Tobby"},
                ]
            },
        }

    def get_user_public_metrics(self, *, access_token: str, user_id: str):  # noqa: ARG002
        assert user_id == "1001"
        return {
            "id": "1001",
            "username": "Tobby_scraper",
            "public_metrics": {
                "followers_count": 1800,
                "tweet_count": 740,
            },
        }

    def get_user_recent_posts(self, *, access_token: str, user_id: str, max_results: int = 20):  # noqa: ARG002
        assert user_id == "1001"
        return [
            {
                "id": "p1",
                "text": "post 1",
                "created_at": "2026-02-20T00:00:00Z",
                "public_metrics": {"like_count": 20, "reply_count": 3, "retweet_count": 2, "quote_count": 1},
            },
            {
                "id": "p2",
                "text": "post 2",
                "created_at": "2026-02-21T00:00:00Z",
                "public_metrics": {"like_count": 18, "reply_count": 2, "retweet_count": 1, "quote_count": 0},
            },
        ]


def _build_sqlite_session_factory() -> sessionmaker:
    load_models()
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def test_strategy_discovery_creates_pending_candidates(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    get_settings.cache_clear()
    get_token_key.cache_clear()

    session_factory = _build_sqlite_session_factory()
    workspace_id = str(uuid.uuid4())
    fake_x = _FakeStrategyDiscoveryXClient()

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

            result = run_workspace_strategy_discovery(
                session,
                workspace_id=workspace_id,
                x_client=fake_x,
            )
            assert result["status"] == "discovered"
            assert result["pending_count"] == 1
            assert result["discovered"] == 1

            rows = list_pending_strategy_candidates(session, workspace_id=workspace_id, limit=10)
            assert len(rows) == 1
            assert rows[0].account_user_id == "1001"
            assert rows[0].status == "pending"
    finally:
        get_token_key.cache_clear()
        get_settings.cache_clear()


def test_strategy_discovery_candidate_approval_moves_to_watchlist(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    get_settings.cache_clear()
    get_token_key.cache_clear()

    session_factory = _build_sqlite_session_factory()
    workspace_id = str(uuid.uuid4())
    fake_x = _FakeStrategyDiscoveryXClient()
    reviewer_user_id = str(uuid.uuid4())

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
            session.add(
                User(
                    id=reviewer_user_id,
                    email=f"owner-{uuid.uuid4()}@revfirst.io",
                    password_hash="hash",
                    is_active=True,
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
            run_workspace_strategy_discovery(
                session,
                workspace_id=workspace_id,
                x_client=fake_x,
            )
            candidate = session.scalar(
                select(XStrategyDiscoveryCandidate).where(XStrategyDiscoveryCandidate.workspace_id == workspace_id)
            )
            assert candidate is not None

            approved = approve_strategy_candidate(
                session,
                workspace_id=workspace_id,
                candidate_id=candidate.id,
                reviewed_by_user_id=reviewer_user_id,
            )
            assert approved is not None
            assert approved["status"] == "approved"

            refreshed = session.scalar(
                select(XStrategyDiscoveryCandidate).where(XStrategyDiscoveryCandidate.id == candidate.id)
            )
            assert refreshed is not None
            assert refreshed.status == "approved"

            watchlist = session.scalar(
                select(XStrategyWatchlist).where(
                    XStrategyWatchlist.workspace_id == workspace_id,
                    XStrategyWatchlist.account_user_id == "1001",
                )
            )
            assert watchlist is not None
            assert watchlist.status == "active"
    finally:
        get_token_key.cache_clear()
        get_settings.cache_clear()
