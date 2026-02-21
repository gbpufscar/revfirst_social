from __future__ import annotations

from sqlalchemy import select

from src.storage.models import WorkspaceControlSetting
from tests.control.conftest import create_control_test_context, teardown_control_test_context


def test_mode_defaults_to_semi_autonomous_and_can_transition(monkeypatch, tmp_path) -> None:
    context = create_control_test_context(monkeypatch, tmp_path)
    try:
        mode_response = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 4101,
                "message": {
                    "message_id": 901,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": "/mode",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert mode_response.status_code == 200
        mode_payload = mode_response.json()
        assert mode_payload["accepted"] is True
        assert mode_payload["message"] == "mode_ok"
        assert mode_payload["data"]["mode"] == "semi_autonomous"

        set_manual = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 4102,
                "message": {
                    "message_id": 902,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": "/mode set manual",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert set_manual.status_code == 200
        set_manual_payload = set_manual.json()
        assert set_manual_payload["accepted"] is True
        assert set_manual_payload["message"] == "mode_updated"
        assert set_manual_payload["data"]["mode"] == "manual"
        assert context.fake_redis.get(f"revfirst:{context.workspace_id}:control:mode") == "manual"

        with context.session_factory() as session:
            row = session.scalar(
                select(WorkspaceControlSetting).where(WorkspaceControlSetting.workspace_id == context.workspace_id)
            )
            assert row is not None
            assert row.operational_mode == "manual"
            assert row.mode_changed_by_user_id == context.owner_user_id
    finally:
        teardown_control_test_context()


def test_mode_set_autonomous_limited_requires_confirmation(monkeypatch, tmp_path) -> None:
    context = create_control_test_context(monkeypatch, tmp_path)
    try:
        missing_confirm = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 4103,
                "message": {
                    "message_id": 903,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": "/mode set autonomous_limited",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert missing_confirm.status_code == 200
        missing_payload = missing_confirm.json()
        assert missing_payload["accepted"] is False
        assert missing_payload["message"] == "mode_set_requires_confirmation"

        confirmed = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 4104,
                "message": {
                    "message_id": 904,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": "/mode set autonomous_limited confirm",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert confirmed.status_code == 200
        confirmed_payload = confirmed.json()
        assert confirmed_payload["accepted"] is True
        assert confirmed_payload["message"] == "mode_updated"
        assert confirmed_payload["data"]["mode"] == "autonomous_limited"
    finally:
        teardown_control_test_context()
