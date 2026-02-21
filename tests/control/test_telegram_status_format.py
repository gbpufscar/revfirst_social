from __future__ import annotations

import src.control.telegram_bot as control_bot_module


def _base_status_payload() -> dict:
    return {
        "mode": "semi_autonomous",
        "global_kill_switch": False,
        "telegram_status": "HEALTHY",
        "channels": {"x": True, "blog": False, "email": False, "instagram": False},
        "last_runs": {
            "daily_post": {
                "status": "succeeded",
            }
        },
        "editorial_stock": {
            "pending_review_count": 2,
            "approved_scheduled_count": 3,
            "next_window_utc": "2026-02-22T16:30:00+00:00",
            "coverage_days": 1.0,
        },
        "recent_errors": [],
    }


def test_status_render_contains_required_sections() -> None:
    rendered = control_bot_module._render_status_reply(_base_status_payload())

    assert rendered.startswith("🔎 SYSTEM STATUS\n----------------")
    assert "\n\nMode:\n" in rendered
    assert "\n\nScheduler:\n" in rendered
    assert "\n\nPublishing:\n" in rendered
    assert "\n\nQueue:\n" in rendered
    assert "\n\nNext Window:\n" in rendered
    assert "\n\nCoverage:\n" in rendered
    assert "\n\nRisk Level:\n" in rendered
    assert "Pending Review: 2" in rendered
    assert "Approved Scheduled: 3" in rendered
    assert "16:30 UTC" in rendered


def test_status_risk_level_low() -> None:
    rendered = control_bot_module._render_status_reply(_base_status_payload())
    assert rendered.endswith("LOW")


def test_status_risk_level_medium_when_recent_errors() -> None:
    payload = _base_status_payload()
    payload["recent_errors"] = [{"source": "publish", "message": "X publish failed"}]
    rendered = control_bot_module._render_status_reply(payload)
    assert rendered.endswith("MEDIUM")


def test_status_risk_level_high_when_mode_containment() -> None:
    payload = _base_status_payload()
    payload["mode"] = "containment"
    rendered = control_bot_module._render_status_reply(payload)
    assert rendered.endswith("HIGH")


def test_status_risk_level_critical_when_global_kill_switch() -> None:
    payload = _base_status_payload()
    payload["global_kill_switch"] = True
    rendered = control_bot_module._render_status_reply(payload)
    assert rendered.endswith("CRITICAL")
