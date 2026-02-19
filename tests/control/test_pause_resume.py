from __future__ import annotations

from sqlalchemy import select

from src.storage.models import WorkspaceControlSetting
from tests.control.conftest import create_control_test_context, teardown_control_test_context


def test_pause_and_resume_toggle_workspace_execution_state(monkeypatch, tmp_path) -> None:
    context = create_control_test_context(monkeypatch, tmp_path)
    try:
        pause_response = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 3001,
                "message": {
                    "message_id": 801,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": "/pause",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert pause_response.status_code == 200
        assert pause_response.json()["accepted"] is True
        assert context.fake_redis.get(f"revfirst:{context.workspace_id}:control:paused") == "true"

        with context.session_factory() as session:
            row = session.scalar(
                select(WorkspaceControlSetting).where(WorkspaceControlSetting.workspace_id == context.workspace_id)
            )
            assert row is not None
            assert row.is_paused is True

        resume_response = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 3002,
                "message": {
                    "message_id": 802,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": "/resume",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert resume_response.status_code == 200
        assert resume_response.json()["accepted"] is True
        assert context.fake_redis.get(f"revfirst:{context.workspace_id}:control:paused") is None

        with context.session_factory() as session:
            row = session.scalar(
                select(WorkspaceControlSetting).where(WorkspaceControlSetting.workspace_id == context.workspace_id)
            )
            assert row is not None
            assert row.is_paused is False
    finally:
        teardown_control_test_context()
