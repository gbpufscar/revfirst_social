from __future__ import annotations

from src.schemas.control import ControlWebhookResponse
import src.control.telegram_bot as control_bot_module


def _response(*, message: str, data: dict) -> ControlWebhookResponse:
    return ControlWebhookResponse(
        accepted=True,
        workspace_id="workspace-1",
        request_id="req-alert-1",
        command="status",
        status="ok",
        message=message,
        data=data,
    )


def _assert_alert_shell(rendered: str) -> None:
    assert rendered.startswith("🚨 ALERT\nType:\n")
    assert "\n\nWorkspace:\n" in rendered
    assert "\n\nAction:\n" in rendered
    assert "\n\nRequired:\n" in rendered


def test_render_stability_warning_alert_preserves_severity() -> None:
    rendered = control_bot_module._render_chat_reply(
        _response(
            message="stability_report_ok",
            data={
                "workspace_id": "ws-123",
                "overall_status": "warning",
                "critical_count": 0,
                "warning_count": 2,
                "recommended_actions": ["Validar scheduler"],
                "kill_switch_action": {"applied": False},
            },
        )
    )

    _assert_alert_shell(rendered)
    assert "Type:\nStability (HIGH)" in rendered
    assert "Workspace:\nws-123" in rendered


def test_render_stability_critical_alert_requires_containment() -> None:
    rendered = control_bot_module._render_chat_reply(
        _response(
            message="stability_report_ok",
            data={
                "workspace_id": "ws-crit",
                "overall_status": "critical",
                "critical_count": 2,
                "warning_count": 0,
                "recommended_actions": [],
                "kill_switch_action": {"applied": False},
            },
        )
    )

    _assert_alert_shell(rendered)
    assert "Type:\nStability (CRITICAL)" in rendered
    assert "Required:\n/stability contain" in rendered


def test_render_publish_failure_alert_uses_publish_status_rate_limit() -> None:
    rendered = control_bot_module._render_chat_reply(
        _response(
            message="approve_publish_failed",
            data={
                "workspace_id": "ws-rl",
                "publish_status": "blocked_rate_limit",
                "error": "X publish failed",
            },
        )
    )

    _assert_alert_shell(rendered)
    assert "Type:\nRate Limit" in rendered
    assert "Workspace:\nws-rl" in rendered


def test_render_publish_failure_alert_uses_publish_status_circuit_breaker() -> None:
    rendered = control_bot_module._render_chat_reply(
        _response(
            message="approve_publish_failed",
            data={
                "workspace_id": "ws-cb",
                "publish_status": "blocked_circuit_breaker",
                "error": "X publish failed",
            },
        )
    )

    _assert_alert_shell(rendered)
    assert "Type:\nCircuit Breaker" in rendered
    assert "Required:\n/stability e diagnostico antes de retomar." in rendered


def test_render_containment_requires_admin_alert_format() -> None:
    rendered = control_bot_module._render_chat_reply(
        _response(
            message="stability_containment_requires_admin",
            data={"workspace_id": "ws-1"},
        )
    )

    _assert_alert_shell(rendered)
    assert "Type:\nContainment Authorization" in rendered
    assert "Required:\nUse owner/admin account." in rendered
