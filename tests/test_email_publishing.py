from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.billing.plans import record_usage
from src.channels.email.publisher import EmailPublisher
from src.publishing.service import publish_email
from src.storage.db import Base, load_models
from src.storage.models import PublishAuditLog, Workspace, WorkspaceDailyUsage, WorkspaceEvent


class _FakeResendClient:
    def __init__(self) -> None:
        self.calls = []
        self._counter = 0

    def send_email(self, *, from_address: str, to: list[str], subject: str, text: str, tags=None):
        self._counter += 1
        self.calls.append(
            {
                "from_address": from_address,
                "to": list(to),
                "subject": subject,
                "text": text,
                "tags": dict(tags or {}),
            }
        )
        return {"id": f"email-{self._counter}"}


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


def test_email_publisher_uses_provider_client() -> None:
    fake_client = _FakeResendClient()
    publisher = EmailPublisher(
        resend_client=fake_client,
        from_address="noreply@revfirst.cloud",
        default_recipients=["default@revfirst.io"],
    )

    from src.channels.base import ChannelPayload

    result = publisher.publish(
        ChannelPayload(
            workspace_id=str(uuid.uuid4()),
            channel="email",
            title="Founder update",
            body="This is a weekly summary.",
            metadata={"recipients": ["team@revfirst.io"]},
        )
    )
    assert result.published is True
    assert result.status == "published"
    assert result.external_id == "email-1"
    assert fake_client.calls[0]["to"] == ["team@revfirst.io"]


def test_publish_email_records_usage_and_audit(monkeypatch) -> None:
    monkeypatch.setenv("PLANS_FILE_PATH", "config/plans.yaml")
    session = _build_session()
    try:
        workspace = Workspace(
            id=str(uuid.uuid4()),
            name="email-publish-workspace",
            plan="free",
            subscription_status="active",
        )
        session.add(workspace)
        session.commit()

        fake_client = _FakeResendClient()
        publisher = EmailPublisher(
            resend_client=fake_client,
            from_address="noreply@revfirst.cloud",
            default_recipients=["default@revfirst.io"],
        )

        result = publish_email(
            session,
            workspace_id=workspace.id,
            subject="Weekly RevFirst report",
            body="Builder-first summary.",
            recipients=["ops@revfirst.io"],
            email_publisher=publisher,
            source_kind="daily_post_draft",
            source_ref_id="draft-1",
        )
        assert result.published is True
        assert result.status == "published"
        assert result.external_post_id == "email-1"

        usage_row = session.scalar(
            select(WorkspaceDailyUsage).where(
                WorkspaceDailyUsage.workspace_id == workspace.id,
                WorkspaceDailyUsage.action == "publish_email",
                WorkspaceDailyUsage.usage_date == datetime.now(timezone.utc).date(),
            )
        )
        assert usage_row is not None
        assert usage_row.count == 1

        audit = session.scalar(
            select(PublishAuditLog)
            .where(
                PublishAuditLog.workspace_id == workspace.id,
                PublishAuditLog.action == "publish_email",
            )
            .order_by(PublishAuditLog.created_at.desc())
        )
        assert audit is not None
        assert audit.platform == "email"
        assert audit.status == "published"
        assert audit.external_post_id == "email-1"

        event = session.scalar(
            select(WorkspaceEvent)
            .where(
                WorkspaceEvent.workspace_id == workspace.id,
                WorkspaceEvent.event_type == "publish_email",
            )
            .order_by(WorkspaceEvent.created_at.desc())
        )
        assert event is not None
    finally:
        session.close()


def test_publish_email_blocks_when_plan_limit_exceeded(monkeypatch) -> None:
    monkeypatch.setenv("PLANS_FILE_PATH", "config/plans.yaml")
    session = _build_session()
    try:
        workspace = Workspace(
            id=str(uuid.uuid4()),
            name="email-plan-block-workspace",
            plan="free",
            subscription_status="active",
        )
        session.add(workspace)
        session.commit()

        record_usage(
            session,
            workspace_id=workspace.id,
            action="publish_email",
            amount=1,
            payload={"seed": "existing"},
        )
        session.commit()

        fake_client = _FakeResendClient()
        publisher = EmailPublisher(
            resend_client=fake_client,
            from_address="noreply@revfirst.cloud",
            default_recipients=["ops@revfirst.io"],
        )

        result = publish_email(
            session,
            workspace_id=workspace.id,
            subject="Blocked by limit",
            body="This should not send.",
            recipients=["ops@revfirst.io"],
            email_publisher=publisher,
        )
        assert result.published is False
        assert result.status == "blocked_plan"
        assert result.message == "Plan limit exceeded"
        assert fake_client.calls == []

        usage_row = session.scalar(
            select(WorkspaceDailyUsage).where(
                WorkspaceDailyUsage.workspace_id == workspace.id,
                WorkspaceDailyUsage.action == "publish_email",
                WorkspaceDailyUsage.usage_date == datetime.now(timezone.utc).date(),
            )
        )
        assert usage_row is not None
        assert usage_row.count == 1
    finally:
        session.close()
