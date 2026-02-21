from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

import src.orchestrator.pipeline as orchestrator_pipeline
from src.core.config import get_settings
from src.orchestrator.pipeline import run_workspace_pipeline
from src.storage.db import Base, load_models
from src.storage.models import ApprovalQueueItem, DailyPostDraft, IngestionCandidate, Workspace, WorkspaceEvent
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
        lambda _: {
            "reply_draft": {"text": "Thanks for sharing your startup. What is your current bottleneck?"},
            "brand_consistency": {"passed": True},
            "cringe_guard": {"cringe": False},
        },
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
            assert result["queued_reply_candidates"] >= 1
            assert result["daily_post_queue"]["status"] in {"ready", "blocked_guard", "skipped_recent_draft"}

            candidates = session.scalars(
                select(IngestionCandidate).where(IngestionCandidate.workspace_id == workspace_id)
            ).all()
            assert len(candidates) == 1

            queued_items = session.scalars(
                select(ApprovalQueueItem).where(ApprovalQueueItem.workspace_id == workspace_id)
            ).all()
            assert len(queued_items) >= 1

            daily_drafts = session.scalars(
                select(DailyPostDraft).where(DailyPostDraft.workspace_id == workspace_id)
            ).all()
            assert len(daily_drafts) >= 1

            events = session.scalars(
                select(WorkspaceEvent).where(WorkspaceEvent.workspace_id == workspace_id)
            ).all()
            assert any(event.event_type == "ingestion_open_calls_run" for event in events)
    finally:
        get_token_key.cache_clear()
        get_settings.cache_clear()


def test_vertical_pipeline_daily_post_respects_interval_and_reply_idempotency(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setattr(
        orchestrator_pipeline,
        "evaluate_candidate_bundle",
        lambda _: {
            "reply_draft": {"text": "Thanks for sharing your startup. What problem are you solving first?"},
            "brand_consistency": {"passed": True},
            "cringe_guard": {"cringe": False},
        },
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

            first = run_workspace_pipeline(
                session,
                workspace_id=workspace_id,
                x_client=fake_x,
            )
            second = run_workspace_pipeline(
                session,
                workspace_id=workspace_id,
                x_client=fake_x,
            )

            assert first["status"] == "executed"
            assert second["status"] == "executed"
            assert second["daily_post_queue"]["status"] == "skipped_recent_draft"

            reply_items = session.scalars(
                select(ApprovalQueueItem).where(
                    ApprovalQueueItem.workspace_id == workspace_id,
                    ApprovalQueueItem.item_type == "reply",
                )
            ).all()
            assert len(reply_items) == 1

            drafts = session.scalars(
                select(DailyPostDraft).where(DailyPostDraft.workspace_id == workspace_id)
            ).all()
            assert len(drafts) == 1
    finally:
        get_token_key.cache_clear()
        get_settings.cache_clear()


def test_vertical_pipeline_executes_growth_and_strategy_agents_when_due(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setattr(
        orchestrator_pipeline,
        "evaluate_candidate_bundle",
        lambda _: {
            "reply_draft": {"text": "Thanks for sharing. What metric moved this week?"},
            "brand_consistency": {"passed": True},
            "cringe_guard": {"cringe": False},
        },
    )
    monkeypatch.setattr(
        orchestrator_pipeline,
        "collect_workspace_growth_snapshot",
        lambda session, *, workspace_id, x_client: {  # noqa: ARG005
            "snapshot_id": "snapshot-1",
            "post_snapshots": 2,
            "errors": [],
        },
    )
    monkeypatch.setattr(
        orchestrator_pipeline,
        "build_workspace_growth_report",
        lambda session, *, workspace_id, period_days=1, persist_insight=True: {  # noqa: ARG005
            "kpis": {
                "period_days": period_days,
                "published_posts": 1,
                "published_replies": 1,
                "failed_publications": 0,
                "follower_delta": 3,
                "engagement": {"samples": 1, "avg_likes": 2.0, "avg_replies": 1.0},
            },
            "recommendations": ["continuar cadence"],
        },
    )
    monkeypatch.setattr(
        orchestrator_pipeline,
        "run_workspace_strategy_scan",
        lambda session, *, workspace_id, x_client: {  # noqa: ARG005
            "status": "scanned",
            "watchlist_count": 1,
            "ingested_posts": 5,
            "recommendations": ["testar hooks diretos"],
            "confidence_score": 12,
            "errors": [],
        },
    )
    monkeypatch.setenv("SCHEDULER_GROWTH_COLLECTION_ENABLED", "true")
    monkeypatch.setenv("SCHEDULER_STRATEGY_SCAN_ENABLED", "true")
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

            assert result["growth_agent"]["status"] == "executed"
            assert result["growth_agent"]["snapshot_id"] == "snapshot-1"
            assert result["strategy_agent"]["status"] == "scanned"
            assert result["strategy_agent"]["watchlist_count"] == 1
    finally:
        get_token_key.cache_clear()
        get_settings.cache_clear()


def test_vertical_pipeline_skips_growth_and_strategy_when_interval_not_due(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setattr(
        orchestrator_pipeline,
        "evaluate_candidate_bundle",
        lambda _: {
            "reply_draft": {"text": "Thanks for sharing. What metric moved this week?"},
            "brand_consistency": {"passed": True},
            "cringe_guard": {"cringe": False},
        },
    )
    monkeypatch.setenv("SCHEDULER_GROWTH_COLLECTION_ENABLED", "true")
    monkeypatch.setenv("SCHEDULER_GROWTH_COLLECTION_INTERVAL_HOURS", "24")
    monkeypatch.setenv("SCHEDULER_STRATEGY_SCAN_ENABLED", "true")
    monkeypatch.setenv("SCHEDULER_STRATEGY_SCAN_INTERVAL_HOURS", "168")
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

            now = datetime.now(timezone.utc)
            session.add(
                WorkspaceEvent(
                    workspace_id=workspace_id,
                    event_type="x_growth_snapshot_collected",
                    payload_json="{}",
                    created_at=now,
                )
            )
            session.add(
                WorkspaceEvent(
                    workspace_id=workspace_id,
                    event_type="x_strategy_scan_completed",
                    payload_json="{}",
                    created_at=now,
                )
            )
            session.commit()

            result = run_workspace_pipeline(
                session,
                workspace_id=workspace_id,
                x_client=fake_x,
            )

            assert result["growth_agent"]["status"] == "skipped_interval"
            assert result["strategy_agent"]["status"] == "skipped_interval"
    finally:
        get_token_key.cache_clear()
        get_settings.cache_clear()
