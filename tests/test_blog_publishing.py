from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.billing.plans import record_usage
from src.channels.blog.publisher import BlogPublisher
from src.publishing.service import publish_blog
from src.storage.db import Base, load_models
from src.storage.models import PublishAuditLog, Workspace, WorkspaceDailyUsage, WorkspaceEvent


class _FakeBlogWebhookClient:
    def __init__(self) -> None:
        self.calls = []
        self._counter = 0

    def publish(self, *, title: str, markdown: str, workspace_id: str, metadata=None):
        self._counter += 1
        self.calls.append(
            {
                "title": title,
                "markdown": markdown,
                "workspace_id": workspace_id,
                "metadata": dict(metadata or {}),
            }
        )
        return {"id": f"blog-{self._counter}"}


def _build_session() -> Session:
    load_models()
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    return factory()


def test_blog_publisher_uses_webhook_client() -> None:
    fake_client = _FakeBlogWebhookClient()
    publisher = BlogPublisher(webhook_client=fake_client)

    from src.channels.base import ChannelPayload

    result = publisher.publish(
        ChannelPayload(
            workspace_id=str(uuid.uuid4()),
            channel="blog",
            title="Founder Operating Guide",
            body="# Header\n\nBody content.",
            metadata={"source_kind": "daily_post_draft"},
        )
    )
    assert result.published is True
    assert result.status == "published"
    assert result.external_id == "blog-1"
    assert fake_client.calls[0]["title"] == "Founder Operating Guide"


def test_publish_blog_records_usage_and_audit(monkeypatch) -> None:
    monkeypatch.setenv("PLANS_FILE_PATH", "config/plans.yaml")
    session = _build_session()
    try:
        workspace = Workspace(
            id=str(uuid.uuid4()),
            name="blog-publish-workspace",
            plan="free",
            subscription_status="active",
        )
        session.add(workspace)
        session.commit()

        fake_client = _FakeBlogWebhookClient()
        publisher = BlogPublisher(webhook_client=fake_client)

        result = publish_blog(
            session,
            workspace_id=workspace.id,
            title="Founder Execution Memo",
            markdown="## Weekly priorities\n\n- Reply strategy\n- Offer clarity",
            blog_publisher=publisher,
            source_kind="daily_post_draft",
            source_ref_id="draft-blog-1",
        )
        assert result.published is True
        assert result.status == "published"
        assert result.external_post_id == "blog-1"

        usage_row = session.scalar(
            select(WorkspaceDailyUsage).where(
                WorkspaceDailyUsage.workspace_id == workspace.id,
                WorkspaceDailyUsage.action == "publish_blog",
                WorkspaceDailyUsage.usage_date == datetime.now(timezone.utc).date(),
            )
        )
        assert usage_row is not None
        assert usage_row.count == 1

        audit = session.scalar(
            select(PublishAuditLog)
            .where(
                PublishAuditLog.workspace_id == workspace.id,
                PublishAuditLog.action == "publish_blog",
            )
            .order_by(PublishAuditLog.created_at.desc())
        )
        assert audit is not None
        assert audit.platform == "blog"
        assert audit.status == "published"
        assert audit.external_post_id == "blog-1"

        event = session.scalar(
            select(WorkspaceEvent)
            .where(
                WorkspaceEvent.workspace_id == workspace.id,
                WorkspaceEvent.event_type == "publish_blog",
            )
            .order_by(WorkspaceEvent.created_at.desc())
        )
        assert event is not None
    finally:
        session.close()


def test_publish_blog_blocks_when_plan_limit_exceeded(monkeypatch) -> None:
    monkeypatch.setenv("PLANS_FILE_PATH", "config/plans.yaml")
    session = _build_session()
    try:
        workspace = Workspace(
            id=str(uuid.uuid4()),
            name="blog-plan-block-workspace",
            plan="free",
            subscription_status="active",
        )
        session.add(workspace)
        session.commit()

        record_usage(
            session,
            workspace_id=workspace.id,
            action="publish_blog",
            amount=1,
            payload={"source": "existing"},
        )
        session.commit()

        fake_client = _FakeBlogWebhookClient()
        publisher = BlogPublisher(webhook_client=fake_client)

        result = publish_blog(
            session,
            workspace_id=workspace.id,
            title="Blocked blog publish",
            markdown="This should not be published.",
            blog_publisher=publisher,
        )
        assert result.published is False
        assert result.status == "blocked_plan"
        assert result.message == "Plan limit exceeded"
        assert fake_client.calls == []
    finally:
        session.close()
