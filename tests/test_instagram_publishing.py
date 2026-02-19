from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.billing.plans import record_usage
from src.channels.instagram.publisher import InstagramPublisher
from src.publishing.service import publish_instagram
from src.storage.db import Base, load_models
from src.storage.models import PublishAuditLog, Workspace, WorkspaceDailyUsage, WorkspaceEvent


class _FakeInstagramGraphClient:
    def __init__(self) -> None:
        self.calls = []
        self._counter = 0

    def publish_caption(self, *, caption: str, image_url: str):
        self._counter += 1
        self.calls.append({"caption": caption, "image_url": image_url})
        return {
            "creation_response": {"id": f"creation-{self._counter}"},
            "publish_response": {"id": f"instagram-{self._counter}"},
        }


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


def test_instagram_publisher_uses_graph_client() -> None:
    fake_client = _FakeInstagramGraphClient()
    publisher = InstagramPublisher(
        graph_client=fake_client,
        default_image_url="https://cdn.revfirst.cloud/default-image.jpg",
    )

    from src.channels.base import ChannelPayload

    result = publisher.publish(
        ChannelPayload(
            workspace_id=str(uuid.uuid4()),
            channel="instagram",
            title=None,
            body="Founder note with one direct takeaway.",
            metadata={"image_url": "https://cdn.revfirst.cloud/custom.jpg"},
        )
    )
    assert result.published is True
    assert result.status == "published"
    assert result.external_id == "instagram-1"
    assert fake_client.calls[0]["image_url"] == "https://cdn.revfirst.cloud/custom.jpg"


def test_publish_instagram_records_usage_and_audit(monkeypatch) -> None:
    monkeypatch.setenv("PLANS_FILE_PATH", "config/plans.yaml")
    session = _build_session()
    try:
        workspace = Workspace(
            id=str(uuid.uuid4()),
            name="instagram-publish-workspace",
            plan="free",
            subscription_status="active",
        )
        session.add(workspace)
        session.commit()

        fake_client = _FakeInstagramGraphClient()
        publisher = InstagramPublisher(graph_client=fake_client)

        result = publish_instagram(
            session,
            workspace_id=workspace.id,
            caption="Builder-first insight with practical context.",
            image_url="https://cdn.revfirst.cloud/post-1.jpg",
            instagram_publisher=publisher,
            source_kind="daily_post_draft",
            source_ref_id="draft-ig-1",
        )
        assert result.published is True
        assert result.status == "published"
        assert result.external_post_id == "instagram-1"

        usage_row = session.scalar(
            select(WorkspaceDailyUsage).where(
                WorkspaceDailyUsage.workspace_id == workspace.id,
                WorkspaceDailyUsage.action == "publish_instagram",
                WorkspaceDailyUsage.usage_date == datetime.now(timezone.utc).date(),
            )
        )
        assert usage_row is not None
        assert usage_row.count == 1

        audit = session.scalar(
            select(PublishAuditLog)
            .where(
                PublishAuditLog.workspace_id == workspace.id,
                PublishAuditLog.action == "publish_instagram",
            )
            .order_by(PublishAuditLog.created_at.desc())
        )
        assert audit is not None
        assert audit.platform == "instagram"
        assert audit.status == "published"
        assert audit.external_post_id == "instagram-1"

        event = session.scalar(
            select(WorkspaceEvent)
            .where(
                WorkspaceEvent.workspace_id == workspace.id,
                WorkspaceEvent.event_type == "publish_instagram",
            )
            .order_by(WorkspaceEvent.created_at.desc())
        )
        assert event is not None
    finally:
        session.close()


def test_publish_instagram_blocks_when_plan_limit_exceeded(monkeypatch) -> None:
    monkeypatch.setenv("PLANS_FILE_PATH", "config/plans.yaml")
    session = _build_session()
    try:
        workspace = Workspace(
            id=str(uuid.uuid4()),
            name="instagram-plan-block-workspace",
            plan="free",
            subscription_status="active",
        )
        session.add(workspace)
        session.commit()

        record_usage(
            session,
            workspace_id=workspace.id,
            action="publish_instagram",
            amount=1,
            payload={"source": "existing"},
        )
        session.commit()

        fake_client = _FakeInstagramGraphClient()
        publisher = InstagramPublisher(graph_client=fake_client)

        result = publish_instagram(
            session,
            workspace_id=workspace.id,
            caption="This should be blocked by plan limit.",
            image_url="https://cdn.revfirst.cloud/post-blocked.jpg",
            instagram_publisher=publisher,
        )
        assert result.published is False
        assert result.status == "blocked_plan"
        assert result.message == "Plan limit exceeded"
        assert fake_client.calls == []
    finally:
        session.close()
