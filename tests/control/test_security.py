from __future__ import annotations

from sqlalchemy import select

from src.storage.models import AdminAction
from tests.control.conftest import create_control_test_context, teardown_control_test_context


def test_control_webhook_blocks_user_outside_whitelist(monkeypatch, tmp_path) -> None:
    context = create_control_test_context(monkeypatch, tmp_path, include_in_allowed=False)
    try:
        response = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 1001,
                "message": {
                    "message_id": 501,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": "/status",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["accepted"] is False
        assert payload["status"] == "unauthorized"

        with context.session_factory() as session:
            audit = session.scalar(
                select(AdminAction)
                .where(AdminAction.workspace_id == context.workspace_id)
                .order_by(AdminAction.created_at.desc())
            )
            assert audit is not None
            assert audit.status == "unauthorized"
            assert audit.command == "status"
    finally:
        teardown_control_test_context()
