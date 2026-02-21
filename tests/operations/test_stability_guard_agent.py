from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.operations.stability_guard_agent as stability_guard_module
from src.control.security import reset_admin_directory_cache
from src.control.state import global_kill_switch_key
from src.core.config import get_settings
from src.storage.db import Base, load_models
from src.storage.models import Workspace, WorkspaceControlSetting


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._expires_at: dict[str, datetime] = {}

    def _prune(self, key: str) -> None:
        expires = self._expires_at.get(key)
        if expires is None:
            return
        if datetime.now(timezone.utc) >= expires:
            self._store.pop(key, None)
            self._expires_at.pop(key, None)

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        self._prune(key)
        if nx and key in self._store:
            return False
        self._store[key] = str(value)
        if ex is not None:
            self._expires_at[key] = datetime.now(timezone.utc) + timedelta(seconds=max(1, int(ex)))
        elif key in self._expires_at:
            self._expires_at.pop(key, None)
        return True

    def get(self, key: str):
        self._prune(key)
        return self._store.get(key)

    def delete(self, key: str):
        self._prune(key)
        self._expires_at.pop(key, None)
        return 1 if self._store.pop(key, None) is not None else 0

    def exists(self, key: str):
        self._prune(key)
        return 1 if key in self._store else 0

    def keys(self, pattern: str):
        if "*" not in pattern:
            self._prune(pattern)
            return [pattern] if pattern in self._store else []
        prefix = pattern.split("*", 1)[0]
        keys = []
        for key in list(self._store.keys()):
            self._prune(key)
            if key.startswith(prefix):
                keys.append(key)
        return keys

    def eval(self, script: str, numkeys: int, key: str, token: str):  # noqa: ARG002
        self._prune(key)
        if self._store.get(key) == token:
            self._store.pop(key, None)
            self._expires_at.pop(key, None)
            return 1
        return 0

    def ttl(self, key: str):
        self._prune(key)
        if key not in self._store:
            return -2
        expires = self._expires_at.get(key)
        if expires is None:
            return -1
        return int(max(0, (expires - datetime.now(timezone.utc)).total_seconds()))


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


def _create_workspace(session) -> str:
    workspace_id = str(uuid.uuid4())
    session.add(
        Workspace(
            id=workspace_id,
            name=f"stability-{workspace_id}",
            plan="free",
            subscription_status="active",
        )
    )
    session.commit()
    return workspace_id


def test_evaluate_kill_switch_criteria_threshold(monkeypatch) -> None:
    monkeypatch.setenv("STABILITY_KILL_SWITCH_CRITERIA_THRESHOLD", "2")
    monkeypatch.setenv("STABILITY_KILL_SWITCH_ENABLED", "true")
    get_settings.cache_clear()

    report = {
        "checks": [
            {"key": "x_oauth_publish_ready", "status": "fail", "details": {}},
            {"key": "publish_failures_24h", "status": "warn", "details": {"failed_count": 3, "consecutive_failures": 1}},
            {"key": "approval_queue_health", "status": "pass", "details": {"publishing_age_minutes": 0}},
            {"key": "lock_health", "status": "pass", "details": {"active_locks": []}},
            {"key": "config_drift", "status": "pass", "details": {"drift_detected": False}},
        ]
    }
    result = stability_guard_module.evaluate_kill_switch_criteria(report)
    assert result["enabled"] is True
    assert result["true_count"] >= 2
    assert result["triggered"] is True
    get_settings.cache_clear()


def test_run_cycle_applies_auto_containment_when_critical(monkeypatch) -> None:
    monkeypatch.setenv("STABILITY_AUTO_CONTAINMENT_ON_CRITICAL", "true")
    monkeypatch.setenv("STABILITY_KILL_SWITCH_ENABLED", "false")
    monkeypatch.setenv("X_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("X_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("X_REDIRECT_URI", "https://social.revfirst.cloud/integrations/x/oauth/callback")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("APP_PUBLIC_BASE_URL", "https://social.revfirst.cloud")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_INTERNAL_KEY", "test-internal")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-encryption-key-1234567890")
    get_settings.cache_clear()

    session_factory = _build_sqlite_session_factory()
    redis_client = _FakeRedis()

    with session_factory() as session:
        workspace_id = _create_workspace(session)
        session.add(
            WorkspaceControlSetting(
                workspace_id=workspace_id,
                is_paused=False,
                operational_mode="semi_autonomous",
                channels_json='{"blog":false,"email":false,"instagram":false,"x":true}',
            )
        )
        session.commit()

        report = stability_guard_module.run_workspace_stability_guard_cycle(
            session,
            workspace_id=workspace_id,
            redis_client=redis_client,
            actor_user_id=None,
            trigger="test",
        )

        assert report["overall_status"] in {"critical", "warning", "healthy"}
        containment = report.get("containment") or {}
        if report["overall_status"] == "critical":
            assert "mode_containment" in containment.get("actions_applied", [])
            assert redis_client.get(f"revfirst:{workspace_id}:control:paused") == "true"
            row = session.scalar(
                select(WorkspaceControlSetting).where(WorkspaceControlSetting.workspace_id == workspace_id)
            )
            assert row is not None
            assert row.operational_mode == "containment"
    get_settings.cache_clear()


def test_kill_switch_ttl_and_ack_flow(monkeypatch) -> None:
    monkeypatch.setenv("STABILITY_KILL_SWITCH_ENABLED", "true")
    monkeypatch.setenv("STABILITY_KILL_SWITCH_CRITERIA_THRESHOLD", "1")
    monkeypatch.setenv("STABILITY_KILL_SWITCH_TTL_SECONDS", "120")
    monkeypatch.setenv("STABILITY_KILL_SWITCH_ACK_TTL_SECONDS", "600")
    get_settings.cache_clear()

    session_factory = _build_sqlite_session_factory()
    redis_client = _FakeRedis()

    with session_factory() as session:
        workspace_id = _create_workspace(session)
        monkeypatch.setattr(
            stability_guard_module,
            "build_workspace_stability_report",
            lambda session, *, workspace_id, redis_client: {  # noqa: ARG005
                "workspace_id": workspace_id,
                "overall_status": "warning",
                "critical_count": 0,
                "warning_count": 1,
                "checks": [],
                "recommended_actions": [],
                "containment_recommended": False,
                "kill_switch": {
                    "enabled": True,
                    "threshold": 1,
                    "true_count": 1,
                    "criteria": {"oauth_invalid": True},
                    "triggered": True,
                    "trigger_reasons": ["oauth_invalid"],
                },
            },
        )

        report = stability_guard_module.run_workspace_stability_guard_cycle(
            session,
            workspace_id=workspace_id,
            redis_client=redis_client,
            actor_user_id=None,
            trigger="test",
        )
        assert report["kill_switch_action"]["applied"] is True
        ttl_after_trigger = redis_client.ttl(global_kill_switch_key())
        assert 0 < ttl_after_trigger <= 120

        ack = stability_guard_module.ack_global_kill_switch(
            session,
            workspace_id=workspace_id,
            redis_client=redis_client,
            actor_user_id="owner-user-id",
        )
        assert ack["acknowledged"] is True
        ttl_after_ack = redis_client.ttl(global_kill_switch_key())
        assert ttl_after_ack > ttl_after_trigger
        assert ttl_after_ack <= 600
    get_settings.cache_clear()


def test_build_report_isolates_check_errors(monkeypatch) -> None:
    session_factory = _build_sqlite_session_factory()
    redis_client = _FakeRedis()

    with session_factory() as session:
        workspace_id = _create_workspace(session)
        monkeypatch.setattr(
            stability_guard_module,
            "_check_lock_health",
            lambda redis_client, *, workspace_id: (_ for _ in ()).throw(RuntimeError("lock_check_broken")),  # noqa: ARG005
        )
        report = stability_guard_module.build_workspace_stability_report(
            session,
            workspace_id=workspace_id,
            redis_client=redis_client,
        )

        assert isinstance(report, dict)
        checks = report.get("checks")
        assert isinstance(checks, list)
        lock_check = next(item for item in checks if item.get("key") == "lock_health")
        assert lock_check["status"] == "error"
        assert report["check_error_count"] >= 1


def test_build_report_marks_notification_channel_degraded(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_ADMINS_FILE_PATH", str(tmp_path / "missing_telegram_admins.yaml"))
    get_settings.cache_clear()
    reset_admin_directory_cache()

    session_factory = _build_sqlite_session_factory()
    redis_client = _FakeRedis()

    with session_factory() as session:
        workspace_id = _create_workspace(session)
        report = stability_guard_module.build_workspace_stability_report(
            session,
            workspace_id=workspace_id,
            redis_client=redis_client,
        )
        checks = report.get("checks")
        assert isinstance(checks, list)
        notification_check = next(item for item in checks if item.get("key") == "telegram_notification_channel")
        assert notification_check["status"] == "warn"
        assert notification_check["summary"] == "Notification channel degraded."

    get_settings.cache_clear()
    reset_admin_directory_cache()
