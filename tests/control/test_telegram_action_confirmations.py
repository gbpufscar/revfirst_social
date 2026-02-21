from __future__ import annotations

from src.schemas.control import ControlWebhookResponse
import src.control.telegram_bot as control_bot_module


def _response(*, message: str, data: dict) -> ControlWebhookResponse:
    return ControlWebhookResponse(
        accepted=True,
        workspace_id="workspace-1",
        request_id="req-1",
        command="approve",
        status="ok",
        message=message,
        data=data,
    )


def test_render_approved_scheduled_confirmation() -> None:
    rendered = control_bot_module._render_chat_reply(
        _response(
            message="approved_scheduled",
            data={
                "queue_id": "1e741595-9ac6-44c1-a9cb-95f2d4b224e9",
                "scheduled_for": "2026-02-22T16:30:00+00:00",
                "status": "approved_scheduled",
            },
        )
    )

    assert rendered.startswith("✅ APPROVED")
    assert "ID: `1e741595`" in rendered
    assert "Scheduled For:\n16:30 UTC" in rendered
    assert "Status:\nApproved Scheduled" in rendered
    assert "Next Window:\n16:30 UTC" in rendered


def test_render_publish_success_confirmation() -> None:
    rendered = control_bot_module._render_chat_reply(
        _response(
            message="approved_and_published",
            data={
                "queue_id": "3cd057b5-738d-4f97-ad50-ad70f19b5e0e",
                "status": "published",
                "external_post_id": "2025032035811590506",
            },
        )
    )

    assert rendered.startswith("✅ PUBLISHED")
    assert "ID: `3cd057b5`" in rendered
    assert "Status:\nPublished" in rendered
    assert "Post ID:\n2025032035811590506" in rendered


def test_render_reject_confirmation() -> None:
    rendered = control_bot_module._render_chat_reply(
        _response(
            message="queue_item_rejected",
            data={
                "queue_id": "1e741595-9ac6-44c1-a9cb-95f2d4b224e9",
                "status": "rejected",
            },
        )
    )

    assert rendered.startswith("❌ REJECTED")
    assert "ID: `1e741595`" in rendered
    assert "Status:\nRejected" in rendered
    assert "Replacement Draft:\nNot generated" in rendered


def test_render_reject_regenerated_confirmation() -> None:
    rendered = control_bot_module._render_chat_reply(
        _response(
            message="queue_item_rejected_regenerated",
            data={
                "queue_id": "1e741595-9ac6-44c1-a9cb-95f2d4b224e9",
                "status": "rejected",
                "auto_regeneration": {
                    "triggered": True,
                    "queue_id": "fa6b0fc1-8f06-46a5-9029-a09d953b502c",
                },
            },
        )
    )

    assert rendered.startswith("❌ REJECTED")
    assert "ID: `1e741595`" in rendered
    assert "Status:\nRejected" in rendered
    assert "Replacement Draft:\nGenerated (fa6b0fc1)" in rendered
