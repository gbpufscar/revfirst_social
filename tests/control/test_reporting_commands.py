from __future__ import annotations

from datetime import datetime, timezone
import uuid

from src.storage.models import ApprovalQueueItem, PublishAuditLog, WorkspaceDailyUsage
from tests.control.conftest import create_control_test_context, teardown_control_test_context


def _seed_reporting_rows(context) -> None:
    today = datetime.now(timezone.utc).date()
    with context.session_factory() as session:
        session.add(
            WorkspaceDailyUsage(
                id=str(uuid.uuid4()),
                workspace_id=context.workspace_id,
                usage_date=today,
                action="publish_reply",
                count=3,
            )
        )
        session.add(
            WorkspaceDailyUsage(
                id=str(uuid.uuid4()),
                workspace_id=context.workspace_id,
                usage_date=today,
                action="publish_post",
                count=1,
            )
        )
        session.add(
            PublishAuditLog(
                id=str(uuid.uuid4()),
                workspace_id=context.workspace_id,
                platform="x",
                action="publish_post",
                request_text="Founder update",
                status="published",
                payload_json="{}",
            )
        )
        session.add(
            PublishAuditLog(
                id=str(uuid.uuid4()),
                workspace_id=context.workspace_id,
                platform="instagram",
                action="publish_instagram",
                request_text="Founder visual",
                status="failed",
                error_message="instagram_image_url_missing",
                payload_json="{}",
            )
        )
        session.add(
            ApprovalQueueItem(
                id=str(uuid.uuid4()),
                workspace_id=context.workspace_id,
                item_type="reply",
                status="pending_review",
                content_text="Queue sample",
                source_kind="test",
                source_ref_id="sample",
                intent="open_call",
                opportunity_score=75,
                metadata_json="{}",
                idempotency_key="reporting-test-queue",
            )
        )
        session.commit()


def test_daily_report_command_returns_generated_report(monkeypatch, tmp_path) -> None:
    context = create_control_test_context(monkeypatch, tmp_path)
    try:
        _seed_reporting_rows(context)
        response = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 5101,
                "message": {
                    "message_id": 1001,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": "/daily_report",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["accepted"] is True
        assert payload["message"] == "daily_report_ok"
        assert payload["data"]["period"] == "daily"
        assert payload["data"]["usage"]["publish_reply"] == 3
        assert "recommendations" in payload["data"]
        assert isinstance(payload["data"]["recommendations"], list)
    finally:
        teardown_control_test_context()


def test_weekly_report_command_returns_generated_report(monkeypatch, tmp_path) -> None:
    context = create_control_test_context(monkeypatch, tmp_path)
    try:
        _seed_reporting_rows(context)
        response = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 5102,
                "message": {
                    "message_id": 1002,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": "/weekly_report",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["accepted"] is True
        assert payload["message"] == "weekly_report_ok"
        assert payload["data"]["period"] == "weekly"
        assert "start_date" in payload["data"]
        assert "end_date" in payload["data"]
        assert isinstance(payload["data"]["publish"]["by_platform"], dict)
    finally:
        teardown_control_test_context()
