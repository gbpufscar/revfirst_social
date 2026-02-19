from __future__ import annotations

from sqlalchemy import select

from src.storage.models import PipelineRun
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
