from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from sqlalchemy import select

import src.channels.instagram.publisher as instagram_publisher_module
import src.control.telegram_bot as control_bot_module
import src.control.handlers.strategy as strategy_handler_module
from src.control.services import create_queue_item
from src.storage.models import ApprovalQueueItem, PipelineRun
from tests.control.conftest import create_control_test_context, teardown_control_test_context


def test_control_router_sends_chat_reply_when_command_processed(monkeypatch, tmp_path) -> None:
    sent_messages: list[dict[str, str]] = []

    def _fake_send_telegram_chat_message(*, chat_id: str, text: str) -> None:
        sent_messages.append({"chat_id": chat_id, "text": text})

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:phase12-test-token")
    monkeypatch.setattr(control_bot_module, "_send_telegram_chat_message", _fake_send_telegram_chat_message)

    context = create_control_test_context(monkeypatch, tmp_path)
    try:
        response = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 1999,
                "message": {
                    "message_id": 600,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": "/status",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["accepted"] is True
        assert payload["message"] == "status_ok"

        assert len(sent_messages) == 1
        assert sent_messages[0]["chat_id"] == "7001"
        assert "Status do workspace" in sent_messages[0]["text"]
        assert "/queue" in sent_messages[0]["text"]
    finally:
        teardown_control_test_context()


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


def test_control_router_strategy_discover_run(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        strategy_handler_module,
        "run_workspace_strategy_discovery",
        lambda session, *, workspace_id, x_client: {  # noqa: ARG005
            "workspace_id": workspace_id,
            "status": "discovered",
            "scanned_users": 4,
            "discovered": 2,
            "updated": 1,
            "pending_count": 2,
            "candidates": [],
            "errors": [],
        },
    )

    context = create_control_test_context(monkeypatch, tmp_path)
    try:
        response = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 21011,
                "message": {
                    "message_id": 801,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": "/strategy_discover run",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["accepted"] is True
        assert payload["message"] == "strategy_discovery_ok"
        assert payload["data"]["pending_count"] == 2
    finally:
        teardown_control_test_context()


def test_control_router_strategy_discover_queue_and_approve(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        strategy_handler_module,
        "list_pending_strategy_candidates",
        lambda session, *, workspace_id, limit=10: [  # noqa: ARG005
            SimpleNamespace(
                id="cand-123",
                account_user_id="954379895022473223",
                account_username="Tobby_scraper",
                score=78,
                followers_count=1200,
                signal_post_count=3,
                status="pending",
                discovered_at=datetime(2026, 2, 21, 0, 0, tzinfo=timezone.utc),
            )
        ],
    )
    monkeypatch.setattr(
        strategy_handler_module,
        "approve_strategy_candidate",
        lambda session, *, workspace_id, candidate_id, reviewed_by_user_id: {  # noqa: ARG005
            "candidate_id": candidate_id,
            "account_user_id": "954379895022473223",
            "account_username": "Tobby_scraper",
            "status": "approved",
            "watchlist_status": "active",
        },
    )

    context = create_control_test_context(monkeypatch, tmp_path)
    try:
        queue_response = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 21012,
                "message": {
                    "message_id": 802,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": "/strategy_discover queue",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert queue_response.status_code == 200
        queue_payload = queue_response.json()
        assert queue_payload["accepted"] is True
        assert queue_payload["message"] == "strategy_candidates_queue"
        assert queue_payload["data"]["count"] == 1
        assert queue_payload["data"]["items"][0]["candidate_id"] == "cand-123"

        approve_response = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 21013,
                "message": {
                    "message_id": 803,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": "/strategy_discover approve cand-123",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert approve_response.status_code == 200
        approve_payload = approve_response.json()
        assert approve_payload["accepted"] is True
        assert approve_payload["message"] == "strategy_candidate_approved"
        assert approve_payload["data"]["candidate_id"] == "cand-123"
    finally:
        teardown_control_test_context()


def test_control_router_preview_sends_photo_for_queue_item(monkeypatch, tmp_path) -> None:
    sent_messages: list[dict[str, str]] = []
    sent_photos: list[dict[str, str | None]] = []

    def _fake_send_telegram_chat_message(*, chat_id: str, text: str) -> None:
        sent_messages.append({"chat_id": chat_id, "text": text})

    def _fake_send_telegram_chat_photo(*, chat_id: str, image_url: str, caption: str | None = None) -> None:
        sent_photos.append({"chat_id": chat_id, "image_url": image_url, "caption": caption})

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:phase12-test-token")
    monkeypatch.setattr(control_bot_module, "_send_telegram_chat_message", _fake_send_telegram_chat_message)
    monkeypatch.setattr(control_bot_module, "_send_telegram_chat_photo", _fake_send_telegram_chat_photo)

    context = create_control_test_context(monkeypatch, tmp_path)
    try:
        with context.session_factory() as session:
            queue_item = create_queue_item(
                session,
                workspace_id=context.workspace_id,
                item_type="post",
                content_text="Founder note with image preview and clear CTA.",
                source_kind="manual_test",
                source_ref_id="preview-1",
                intent="daily_post",
                opportunity_score=95,
                idempotency_key="test-preview-photo-1",
                metadata={"image_url": "https://cdn.revfirst.cloud/preview-1.png"},
            )
            queue_id = queue_item.id

        response = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 2102,
                "message": {
                    "message_id": 702,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": f"/preview {queue_id}",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["accepted"] is True
        assert payload["message"] == "preview_ready"
        assert payload["data"]["queue_id"] == queue_id
        assert payload["data"]["image_url"] == "https://cdn.revfirst.cloud/preview-1.png"

        assert len(sent_photos) == 1
        assert sent_photos[0]["chat_id"] == "7001"
        assert sent_photos[0]["image_url"] == "https://cdn.revfirst.cloud/preview-1.png"
        assert queue_id in str(sent_photos[0]["caption"])

        assert len(sent_messages) == 1
        assert "Preview pronto para POST" in sent_messages[0]["text"]
    finally:
        teardown_control_test_context()


def test_control_router_plain_text_sim_approves_latest_pending_item(monkeypatch, tmp_path) -> None:
    context = create_control_test_context(monkeypatch, tmp_path)
    try:
        manual_token = context.client.post(
            "/integrations/x/oauth/token/manual",
            json={
                "workspace_id": context.workspace_id,
                "access_token": "phase12-x-access-token",
                "refresh_token": "phase12-x-refresh-token",
                "expires_in": 3600,
                "scope": "tweet.read tweet.write users.read",
            },
            headers={"Authorization": f"Bearer {context.access_token}"},
        )
        assert manual_token.status_code == 200

        with context.session_factory() as session:
            queue_item = create_queue_item(
                session,
                workspace_id=context.workspace_id,
                item_type="post",
                content_text="Founder update for plain text approval flow.",
                source_kind="manual_test",
                source_ref_id="plain-sim-1",
                intent="daily_post",
                opportunity_score=88,
                idempotency_key="test-plain-sim-approval-1",
                metadata={},
            )
            queue_id = queue_item.id

        response = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 2103,
                "message": {
                    "message_id": 703,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": "sim",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["accepted"] is True
        assert payload["message"] == "approved_and_published"
        assert payload["data"]["queue_id"] == queue_id
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
