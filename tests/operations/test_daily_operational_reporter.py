from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.operations.daily_operational_reporter as reporter_module
import src.orchestrator.pipeline as orchestrator_pipeline
from src.core.config import get_settings
from src.orchestrator.pipeline import run_workspace_pipeline
from src.storage.db import Base, load_models
from src.storage.models import Workspace, WorkspaceEvent


def _build_sqlite_session_factory():
    load_models()
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _create_workspace(session, *, name_prefix: str = "ops-report") -> str:
    workspace_id = str(uuid.uuid4())
    session.add(
        Workspace(
            id=workspace_id,
            name=f"{name_prefix}-{workspace_id}",
            plan="free",
            subscription_status="active",
        )
    )
    session.commit()
    return workspace_id


def test_classify_risk_level_deterministic_ordering() -> None:
    assert (
        reporter_module.classify_risk_level(
            stability_critical_count=0,
            auto_containments=0,
            kill_switch_activations=1,
            rate_limit_blocks=0,
            consecutive_failure_triggers=0,
            success_rate_pct=100,
        )
        == "CRITICAL"
    )
    assert (
        reporter_module.classify_risk_level(
            stability_critical_count=1,
            auto_containments=0,
            kill_switch_activations=0,
            rate_limit_blocks=0,
            consecutive_failure_triggers=0,
            success_rate_pct=100,
        )
        == "CRITICAL"
    )
    assert (
        reporter_module.classify_risk_level(
            stability_critical_count=0,
            auto_containments=0,
            kill_switch_activations=0,
            rate_limit_blocks=1,
            consecutive_failure_triggers=0,
            success_rate_pct=100,
        )
        == "HIGH"
    )
    assert (
        reporter_module.classify_risk_level(
            stability_critical_count=0,
            auto_containments=0,
            kill_switch_activations=0,
            rate_limit_blocks=0,
            consecutive_failure_triggers=0,
            success_rate_pct=79,
        )
        == "MEDIUM"
    )
    assert (
        reporter_module.classify_risk_level(
            stability_critical_count=0,
            auto_containments=0,
            kill_switch_activations=0,
            rate_limit_blocks=0,
            consecutive_failure_triggers=0,
            success_rate_pct=80,
        )
        == "LOW"
    )


def test_format_daily_operational_report_strict_block() -> None:
    payload = {
        "date_utc": "2026-02-21",
        "mode": "semi_autonomous",
        "editorial_stock": {
            "pending_review_count": 2,
            "approved_scheduled_count": 3,
            "next_window_utc": "2026-02-21T16:30:00+00:00",
            "coverage_days": 1.0,
        },
        "stability": {
            "reports": 2,
            "critical": 1,
            "high": 1,
            "auto_containments": 1,
            "kill_switch": 0,
        },
        "publishing": {
            "attempts": 10,
            "success": 8,
            "failures": 2,
            "success_rate_pct": 80,
        },
        "circuit_breakers": {
            "rate_limit_blocks": 1,
            "consecutive_failure_triggers": 0,
        },
        "x_rate_limits": {"http_429_count": 3},
        "redis": {"active_locks": 2, "ttl_anomalies": 1},
        "risk_assessment": "HIGH",
    }
    expected = (
        "DAILY OPERATIONAL REPORT\n"
        "------------------------\n"
        "Date: 2026-02-21 (UTC)\n"
        "\n"
        "Mode: semi_autonomous\n"
        "\n"
        "Editorial Stock:\n"
        "  Pending Review: 2\n"
        "  Approved Scheduled: 3\n"
        "  Next Window (UTC): 2026-02-21T16:30:00+00:00\n"
        "  Coverage Days: 1.0\n"
        "\n"
        "Stability:\n"
        "  Reports: 2\n"
        "  CRITICAL: 1\n"
        "  HIGH: 1\n"
        "  Auto-Containments: 1\n"
        "  Kill-Switch: 0\n"
        "\n"
        "Publishing:\n"
        "  Attempts: 10\n"
        "  Success: 8\n"
        "  Failures: 2\n"
        "  Success Rate: 80%\n"
        "\n"
        "Circuit Breakers:\n"
        "  Rate Limit Blocks: 1\n"
        "  Consecutive Failure Triggers: 0\n"
        "\n"
        "Rate Limits (X):\n"
        "  429 Responses: 3\n"
        "\n"
        "Redis:\n"
        "  Active Locks: 2\n"
        "  TTL Anomalies: 1\n"
        "\n"
        "Risk Assessment: HIGH"
    )
    assert reporter_module.format_daily_operational_report(payload) == expected


def test_reporter_handles_empty_dataset() -> None:
    session_factory = _build_sqlite_session_factory()
    now = datetime(2026, 2, 21, 0, 10, tzinfo=timezone.utc)

    with session_factory() as session:
        workspace_id = _create_workspace(session, name_prefix="empty")
        result = reporter_module.run_daily_operational_report(
            session,
            workspace_id=workspace_id,
            redis_client=None,
            now=now,
        )

    assert result["status"] == "ok"
    snapshot = result["snapshot"]
    assert snapshot["date_utc"] == "2026-02-21"
    assert snapshot["stability"]["reports"] == 0
    assert snapshot["editorial_stock"]["pending_review_count"] == 0
    assert snapshot["editorial_stock"]["approved_scheduled_count"] == 0
    assert snapshot["publishing"]["attempts"] == 0
    assert snapshot["publishing"]["success_rate_pct"] == 0
    assert snapshot["circuit_breakers"]["rate_limit_blocks"] == 0
    assert snapshot["x_rate_limits"]["http_429_count"] == 0
    assert snapshot["redis"]["active_locks"] == 0
    assert snapshot["risk_assessment"] == "MEDIUM"


def test_reporter_telegram_failure_is_non_fatal(monkeypatch) -> None:
    session_factory = _build_sqlite_session_factory()
    now = datetime(2026, 2, 21, 0, 10, tzinfo=timezone.utc)

    monkeypatch.setattr(reporter_module, "_owner_admin_chat_ids", lambda session, workspace_id: ["1023898189"])  # noqa: ARG005

    def _raise_transport(*, chat_id: str, text: str) -> None:  # noqa: ARG001
        raise RuntimeError("telegram transport failed")

    monkeypatch.setattr(reporter_module, "_send_via_control_telegram_service", _raise_transport)

    with session_factory() as session:
        workspace_id = _create_workspace(session, name_prefix="telegram-fail")
        result = reporter_module.run_daily_operational_report(
            session,
            workspace_id=workspace_id,
            redis_client=None,
            now=now,
        )

    assert result["status"] == "ok"
    assert result["delivery"] == {"attempted": 1, "delivered": 0, "failed": 1}


def test_pipeline_hook_runs_reporter_once_per_day(monkeypatch) -> None:
    monkeypatch.setenv("STABILITY_GUARD_SCHEDULER_CHECKS_ENABLED", "false")
    get_settings.cache_clear()

    times = iter(
        [
            datetime(2026, 2, 21, 0, 10, tzinfo=timezone.utc),
            datetime(2026, 2, 21, 12, 30, tzinfo=timezone.utc),
        ]
    )
    call_counter = {"count": 0}

    def _fake_daily_report(session, *, workspace_id: str, redis_client=None, now=None):  # noqa: ANN001,ARG001
        call_counter["count"] += 1
        return {
            "status": "ok",
            "workspace_id": workspace_id,
            "snapshot": {"risk_assessment": "LOW"},
            "delivery": {"attempted": 0, "delivered": 0, "failed": 0},
            "report_text": "report",
        }

    monkeypatch.setattr(orchestrator_pipeline, "_utc_now", lambda: next(times))
    monkeypatch.setattr(orchestrator_pipeline, "run_daily_operational_report", _fake_daily_report)

    session_factory = _build_sqlite_session_factory()

    try:
        with session_factory() as session:
            workspace_id = _create_workspace(session, name_prefix="pipeline-once-day")
            first = run_workspace_pipeline(
                session,
                workspace_id=workspace_id,
                x_client=None,  # type: ignore[arg-type]
            )
            second = run_workspace_pipeline(
                session,
                workspace_id=workspace_id,
                x_client=None,  # type: ignore[arg-type]
            )

            assert first["daily_operational_report"]["status"] == "executed"
            assert second["daily_operational_report"]["status"] == "skipped_not_due"
            assert call_counter["count"] == 1

            events = session.scalars(
                select(WorkspaceEvent).where(
                    WorkspaceEvent.workspace_id == workspace_id,
                    WorkspaceEvent.event_type == "daily_operational_report_sent",
                )
            ).all()
            assert len(events) == 1
    finally:
        get_settings.cache_clear()
