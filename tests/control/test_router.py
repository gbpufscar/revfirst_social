from __future__ import annotations

from datetime import datetime, timedelta, timezone
from sqlalchemy import select

import src.channels.instagram.publisher as instagram_publisher_module
from src.control.services import create_queue_item
from src.storage.models import ApprovalQueueItem, PipelineRun
from tests.control.conftest import create_control_test_context, teardown_control_test_context


def test_control_router_dispatches_help_and_run_commands(monkeypatch, tmp_path) -> None:
    context = create_control_test_context(monkeypatch, tmp_path)
    try:
        help_response = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 2001,
                "message": {
                    "message_id": 601,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": "/help",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert help_response.status_code == 200
        help_payload = help_response.json()
        assert help_payload["accepted"] is True
        assert help_payload["message"] == "available_commands"
        assert "/status" in help_payload["data"]["commands"]

        run_response = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 2002,
                "message": {
                    "message_id": 602,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": "/run propose_replies dry_run=true",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert run_response.status_code == 200
        run_payload = run_response.json()
        assert run_payload["accepted"] is True
        assert run_payload["message"] == "pipeline_executed"
        assert run_payload["data"]["pipeline"] == "propose_replies"
        assert run_payload["data"]["dry_run"] is True

        with context.session_factory() as session:
            run = session.scalar(
                select(PipelineRun)
                .where(PipelineRun.workspace_id == context.workspace_id)
                .order_by(PipelineRun.created_at.desc())
            )
            assert run is not None
            assert run.pipeline_name == "propose_replies"
            assert run.status == "succeeded"
    finally:
        teardown_control_test_context()


def test_control_router_returns_unknown_command(monkeypatch, tmp_path) -> None:
    context = create_control_test_context(monkeypatch, tmp_path)
    try:
        response = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 2101,
                "message": {
                    "message_id": 701,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": "/unknowncmd",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["accepted"] is False
        assert payload["message"] == "unknown_command"
    finally:
        teardown_control_test_context()


def test_run_daily_post_queues_email_when_channel_enabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "noreply@revfirst.cloud")
    monkeypatch.setenv("EMAIL_DEFAULT_RECIPIENTS", "ops@revfirst.io")
    context = create_control_test_context(monkeypatch, tmp_path)
    try:
        manual_seed = context.client.post(
            "/integrations/telegram/seed/manual",
            json={
                "workspace_id": context.workspace_id,
                "text": "Founders improve traction with direct weekly experiments and clear offers.",
                "source_chat_id": "manual-chat",
                "source_message_id": "manual-seed-1",
            },
            headers={"Authorization": f"Bearer {context.access_token}"},
        )
        assert manual_seed.status_code == 200

        enable_email = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 2201,
                "message": {
                    "message_id": 721,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": "/channel enable email",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert enable_email.status_code == 200
        assert enable_email.json()["accepted"] is True

        run_daily_post = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 2202,
                "message": {
                    "message_id": 722,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": "/run daily_post",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert run_daily_post.status_code == 200
        payload = run_daily_post.json()
        assert payload["accepted"] is True
        assert payload["message"] == "pipeline_executed"
        assert "email" in payload["data"]["result"]["queued_types"]

        with context.session_factory() as session:
            queued_items = list(
                session.scalars(
                    select(ApprovalQueueItem).where(
                        ApprovalQueueItem.workspace_id == context.workspace_id,
                    )
                ).all()
            )
            item_types = {item.item_type for item in queued_items}
            assert "post" in item_types
            assert "email" in item_types
    finally:
        teardown_control_test_context()


def test_run_daily_post_queues_blog_when_channel_enabled(monkeypatch, tmp_path) -> None:
    context = create_control_test_context(monkeypatch, tmp_path)
    try:
        manual_seed = context.client.post(
            "/integrations/telegram/seed/manual",
            json={
                "workspace_id": context.workspace_id,
                "text": "Builders gain momentum when posts become long-form guides with explicit examples.",
                "source_chat_id": "manual-chat",
                "source_message_id": "manual-seed-blog-1",
            },
            headers={"Authorization": f"Bearer {context.access_token}"},
        )
        assert manual_seed.status_code == 200

        enable_blog = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 2301,
                "message": {
                    "message_id": 731,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": "/channel enable blog",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert enable_blog.status_code == 200
        assert enable_blog.json()["accepted"] is True

        run_daily_post = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 2302,
                "message": {
                    "message_id": 732,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": "/run daily_post",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert run_daily_post.status_code == 200
        payload = run_daily_post.json()
        assert payload["accepted"] is True
        assert payload["message"] == "pipeline_executed"
        assert "blog" in payload["data"]["result"]["queued_types"]

        with context.session_factory() as session:
            queued_items = list(
                session.scalars(
                    select(ApprovalQueueItem).where(
                        ApprovalQueueItem.workspace_id == context.workspace_id,
                    )
                ).all()
            )
            item_types = {item.item_type for item in queued_items}
            assert "post" in item_types
            assert "blog" in item_types
    finally:
        teardown_control_test_context()


def test_run_daily_post_queues_instagram_when_channel_enabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("INSTAGRAM_DEFAULT_IMAGE_URL", "https://cdn.revfirst.cloud/default-instagram.jpg")
    context = create_control_test_context(monkeypatch, tmp_path)
    try:
        manual_seed = context.client.post(
            "/integrations/telegram/seed/manual",
            json={
                "workspace_id": context.workspace_id,
                "text": "Builder stories can be repurposed into concise captions with clear context.",
                "source_chat_id": "manual-chat",
                "source_message_id": "manual-seed-instagram-1",
            },
            headers={"Authorization": f"Bearer {context.access_token}"},
        )
        assert manual_seed.status_code == 200

        enable_instagram = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 2401,
                "message": {
                    "message_id": 741,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": "/channel enable instagram",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert enable_instagram.status_code == 200
        assert enable_instagram.json()["accepted"] is True

        run_daily_post = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 2402,
                "message": {
                    "message_id": 742,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": "/run daily_post",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert run_daily_post.status_code == 200
        payload = run_daily_post.json()
        assert payload["accepted"] is True
        assert payload["message"] == "pipeline_executed"
        assert "instagram" in payload["data"]["result"]["queued_types"]

        with context.session_factory() as session:
            queued_items = list(
                session.scalars(
                    select(ApprovalQueueItem).where(
                        ApprovalQueueItem.workspace_id == context.workspace_id,
                    )
                ).all()
            )
            item_types = {item.item_type for item in queued_items}
            assert "post" in item_types
            assert "instagram" in item_types
    finally:
        teardown_control_test_context()


class _FakeInstagramGraphClient:
    def __init__(self) -> None:
        self.counter = 0

    def publish_caption(self, *, caption: str, image_url: str):
        del caption, image_url
        self.counter += 1
        return {
            "creation_response": {"id": f"creation-{self.counter}"},
            "publish_response": {"id": f"instagram-{self.counter}"},
        }


def test_run_execute_approved_skips_future_scheduled_instagram(monkeypatch, tmp_path) -> None:
    fake_instagram = _FakeInstagramGraphClient()
    monkeypatch.setattr(instagram_publisher_module, "get_instagram_graph_client", lambda: fake_instagram)
    context = create_control_test_context(monkeypatch, tmp_path)
    try:
        with context.session_factory() as session:
            queue_item = create_queue_item(
                session,
                workspace_id=context.workspace_id,
                item_type="instagram",
                content_text="Scheduled Instagram caption",
                source_kind="manual_test",
                source_ref_id="scheduled-instagram-1",
                intent="daily_post",
                opportunity_score=100,
                idempotency_key="scheduled-instagram-execute-1",
                metadata={
                    "image_url": "https://cdn.revfirst.cloud/ig-scheduled.jpg",
                    "scheduled_for": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
                },
            )
            queue_item.status = "approved"
            session.commit()

        run_execute = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 2501,
                "message": {
                    "message_id": 751,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": "/run execute_approved",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert run_execute.status_code == 200
        payload = run_execute.json()
        assert payload["accepted"] is True
        assert payload["message"] == "pipeline_executed"
        assert payload["data"]["result"]["scheduled_pending"] == 1
        assert payload["data"]["result"]["published"] == 0
        assert fake_instagram.counter == 0
    finally:
        teardown_control_test_context()
