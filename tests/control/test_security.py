from __future__ import annotations

import yaml
from sqlalchemy import select

from src.control.security import get_telegram_notification_channel_status, reset_admin_directory_cache
from src.core.config import get_settings
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


def test_telegram_notification_channel_is_degraded_when_bot_token_missing(monkeypatch, tmp_path) -> None:
    admins_path = tmp_path / "telegram_admins.yaml"
    admins_path.write_text(
        yaml.safe_dump(
            {
                "allowed_telegram_ids": ["90001"],
                "admins": [
                    {
                        "telegram_user_id": "90001",
                        "user_id": "user-id-1",
                        "allowed_roles": ["owner"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TELEGRAM_ADMINS_FILE_PATH", str(admins_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    get_settings.cache_clear()
    reset_admin_directory_cache()

    status = get_telegram_notification_channel_status()
    assert status.degraded is True
    assert "telegram_bot_token_missing" in status.reasons
    assert status.allowed_ids_count == 1
    get_settings.cache_clear()
    reset_admin_directory_cache()


def test_telegram_notification_channel_is_degraded_when_allowed_ids_empty(monkeypatch, tmp_path) -> None:
    admins_path = tmp_path / "telegram_admins.yaml"
    admins_path.write_text(
        yaml.safe_dump(
            {
                "allowed_telegram_ids": [],
                "admins": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TELEGRAM_ADMINS_FILE_PATH", str(admins_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:test-token")
    get_settings.cache_clear()
    reset_admin_directory_cache()

    status = get_telegram_notification_channel_status()
    assert status.degraded is True
    assert "allowed_telegram_ids_empty" in status.reasons
    assert status.has_bot_token is True
    get_settings.cache_clear()
    reset_admin_directory_cache()
