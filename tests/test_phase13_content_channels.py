from __future__ import annotations

from datetime import date
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.domain.agents.reply_writer import generate_reply_draft, reply_draft_to_content_object
from src.domain.content import ContentObject
from src.domain.routing.channel_router import (
    CHANNEL_FLAGS_KEY_TEMPLATE,
    WORKSPACE_PAUSED_KEY_TEMPLATE,
    route_content_object,
)
from src.storage.db import Base, load_models
from src.storage.models import Workspace, WorkspaceDailyUsage


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def hgetall(self, key: str) -> dict[str, str]:
        return self.hashes.get(key, {})


def _build_session_factory() -> sessionmaker:
    load_models()
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def test_phase13_reply_writer_emits_content_object() -> None:
    workspace_id = str(uuid.uuid4())
    draft = generate_reply_draft(
        workspace_id=workspace_id,
        source_tweet_id="190000000000000007",
        source_text="Open thread for founders building B2B SaaS.",
        intent="open_call",
        opportunity_score=78,
    )

    content = reply_draft_to_content_object(draft)
    assert content.workspace_id == workspace_id
    assert content.content_type == "reply"
    assert content.channel_targets == ["x"]
    assert content.source_agent == "reply_writer"
    assert content.metadata["intent"] == "open_call"
    assert content.metadata["in_reply_to_tweet_id"] == "190000000000000007"


def test_phase13_channel_router_respects_flags_pause_and_plan_limit() -> None:
    session_factory = _build_session_factory()
    workspace_id = str(uuid.uuid4())
    redis = _FakeRedis()

    with session_factory() as session:
        session.add(
            Workspace(
                id=workspace_id,
                name=f"phase13-{workspace_id[:8]}",
                plan="free",
                subscription_status="active",
            )
        )
        session.commit()

        content = ContentObject(
            workspace_id=workspace_id,
            content_type="short_post",
            body="Builders win with direct experiments and faster feedback loops.",
            channel_targets=["x", "email", "blog"],
            metadata={"topic": "growth"},
            source_agent="daily_post_writer",
        )

        flags_key = CHANNEL_FLAGS_KEY_TEMPLATE.format(workspace_id=workspace_id)
        redis.hashes[flags_key] = {"x": "0", "email": "1", "blog": "1"}

        enabled = route_content_object(
            session,
            content=content,
            redis_client=redis,
            enforce_plan_limits=False,
        )
        assert enabled.resolved_targets == ["email", "blog"]
        assert enabled.blocked_targets["x"] == "channel_disabled"

        paused_key = WORKSPACE_PAUSED_KEY_TEMPLATE.format(workspace_id=workspace_id)
        redis.values[paused_key] = "true"
        paused = route_content_object(
            session,
            content=content,
            redis_client=redis,
            enforce_plan_limits=False,
        )
        assert paused.paused is True
        assert paused.resolved_targets == []
        assert paused.blocked_targets["x"] == "workspace_paused"

        redis.values.pop(paused_key, None)
        redis.hashes[flags_key] = {"x": "1"}
        session.add(
            WorkspaceDailyUsage(
                workspace_id=workspace_id,
                usage_date=date.today(),
                action="publish_post",
                count=1,
            )
        )
        session.commit()

        plan_limited = route_content_object(
            session,
            content=content,
            redis_client=redis,
            enforce_plan_limits=True,
        )
        assert plan_limited.plan_limited is True
        assert plan_limited.resolved_targets == []
        assert plan_limited.blocked_targets["x"] == "plan_limit_exceeded"
