from __future__ import annotations

from sqlalchemy import select

import src.control.telegram_bot as control_bot_module
from src.control.services import create_queue_item
from src.storage.models import ApprovalQueueItem
from tests.control.conftest import create_control_test_context, teardown_control_test_context


def test_render_queue_empty_format() -> None:
    rendered = control_bot_module._render_queue_reply({"items": []})
    assert rendered == "📋 Approval Queue is empty."


def test_render_queue_single_item_format() -> None:
    rendered = control_bot_module._render_queue_reply(
        {
            "items": [
                {
                    "queue_id": "1e741595-9ac6-44c1-a9cb-95f2d4b224e9",
                    "type": "post",
                    "copy": "Founder copy for approval.",
                    "image_url": "https://cdn.revfirst.cloud/image.jpg",
                    "status": "pending_review",
                }
            ]
        }
    )

    assert rendered.startswith("📋 Approval Queue (1 items)")
    assert "📝 POST" in rendered
    assert "ID: `1e741595`" in rendered
    assert "Copy:\nFounder copy for approval." in rendered
    assert "Imagem:\nhttps://cdn.revfirst.cloud/image.jpg" in rendered
    assert "Status:\nPending Review" in rendered
    assert "/approve 1e741595" in rendered
    assert "/reject 1e741595" in rendered
    assert "/preview 1e741595" in rendered
    assert "/approve_now 1e741595" in rendered


def test_render_queue_multiple_items_format() -> None:
    rendered = control_bot_module._render_queue_reply(
        {
            "items": [
                {
                    "queue_id": "aaaaaaaa-9ac6-44c1-a9cb-95f2d4b224e9",
                    "type": "post",
                    "copy": "First post copy.",
                    "status": "pending_review",
                },
                {
                    "queue_id": "bbbbbbbb-9ac6-44c1-a9cb-95f2d4b224e9",
                    "type": "reply",
                    "copy": "Second reply copy.",
                    "status": "approved_scheduled",
                },
            ]
        }
    )

    assert rendered.startswith("📋 Approval Queue (2 items)")
    assert rendered.count("📝 ") == 2
    assert "ID: `aaaaaaaa`" in rendered
    assert "ID: `bbbbbbbb`" in rendered
    assert "Status:\nApproved Scheduled" in rendered
    assert "\n\n📝 REPLY" in rendered


def test_render_queue_truncates_copy_and_sanitizes_backticks() -> None:
    long_copy = ("A" * 310) + " with `code`"
    rendered = control_bot_module._render_queue_reply(
        {
            "items": [
                {
                    "queue_id": "cccccccc-9ac6-44c1-a9cb-95f2d4b224e9",
                    "type": "post",
                    "copy": long_copy,
                    "status": "pending_review",
                }
            ]
        }
    )

    copy_block = rendered.split("Copy:\n", 1)[1].split("\n\nImagem:\n", 1)[0]
    assert len(copy_block) <= 300
    assert copy_block.endswith("...")
    assert "`" not in copy_block


def test_queue_commands_accept_full_and_short_id(monkeypatch, tmp_path) -> None:
    sent_messages: list[dict[str, str]] = []

    def _fake_send_telegram_chat_message(*, chat_id: str, text: str) -> None:
        sent_messages.append({"chat_id": chat_id, "text": text})

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:phase12-test-token")
    monkeypatch.setattr(control_bot_module, "_send_telegram_chat_message", _fake_send_telegram_chat_message)

    context = create_control_test_context(monkeypatch, tmp_path)
    try:
        with context.session_factory() as session:
            queue_item = create_queue_item(
                session,
                workspace_id=context.workspace_id,
                item_type="post",
                content_text="Queue item for short id approval.",
                source_kind="manual_test",
                source_ref_id="candidate-queue-short-id",
                intent="daily_post",
                opportunity_score=90,
                idempotency_key="test-queue-short-id-1",
                metadata={},
            )
            queue_id = queue_item.id
            short_id = queue_id[:8]

        preview_response = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 6001,
                "message": {
                    "message_id": 1601,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": f"/preview {queue_id}",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert preview_response.status_code == 200
        assert preview_response.json()["message"] == "preview_image_unavailable"

        approve_response = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 6002,
                "message": {
                    "message_id": 1602,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": f"/approve {short_id}",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert approve_response.status_code == 200
        payload = approve_response.json()
        assert payload["accepted"] is True
        assert payload["message"] == "approved_scheduled"

        with context.session_factory() as session:
            row = session.scalar(
                select(ApprovalQueueItem).where(
                    ApprovalQueueItem.workspace_id == context.workspace_id,
                    ApprovalQueueItem.id == queue_id,
                )
            )
            assert row is not None
            assert row.status == "approved_scheduled"
    finally:
        teardown_control_test_context()
