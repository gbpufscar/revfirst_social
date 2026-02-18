"""Observability bootstrap helpers."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

try:  # pragma: no cover - availability depends on runtime image.
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
except Exception:  # pragma: no cover
    sentry_sdk = None
    FastApiIntegration = None

from src.core.config import get_settings
from src.core.logger import get_logger


_SENTRY_INITIALIZED = False
_SENTRY_AVAILABLE = sentry_sdk is not None and FastApiIntegration is not None


def _call_sentry_init(**kwargs: Any) -> None:
    if sentry_sdk is None:  # pragma: no cover
        raise RuntimeError("sentry_sdk is not available")
    sentry_sdk.init(**kwargs)


def init_sentry() -> bool:
    """Initialize Sentry once when DSN is configured."""

    global _SENTRY_INITIALIZED
    if _SENTRY_INITIALIZED:
        return True

    settings = get_settings()
    dsn = settings.sentry_dsn.strip()
    if not dsn:
        return False
    if not _SENTRY_AVAILABLE:
        get_logger("revfirst.observability").warning("sentry_sdk_not_installed")
        return False

    _call_sentry_init(
        dsn=dsn,
        environment=settings.env,
        release=f"{settings.app_name}@{settings.app_version}",
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
        integrations=[FastApiIntegration()],
    )
    _SENTRY_INITIALIZED = True
    get_logger("revfirst.observability").info(
        "sentry_initialized",
        env=settings.env,
        traces_sample_rate=settings.sentry_traces_sample_rate,
    )
    return True


@contextmanager
def sentry_scope(*, workspace_id: str | None = None, request_id: str | None = None):
    """Create a temporary Sentry scope with request/workspace context."""

    if not _SENTRY_AVAILABLE:
        yield
        return

    assert sentry_sdk is not None  # for type-checkers only
    with sentry_sdk.push_scope() as scope:
        context_payload: dict[str, str] = {}
        if workspace_id:
            scope.set_tag("workspace_id", workspace_id)
            context_payload["workspace_id"] = workspace_id
        if request_id:
            scope.set_tag("request_id", request_id)
            context_payload["request_id"] = request_id
        if context_payload:
            scope.set_context("revfirst", context_payload)
        yield


def capture_exception(exc: BaseException) -> None:
    if not _SENTRY_AVAILABLE:
        return
    assert sentry_sdk is not None  # for type-checkers only
    sentry_sdk.capture_exception(exc)


def reset_observability_for_tests() -> None:
    global _SENTRY_INITIALIZED
    _SENTRY_INITIALIZED = False
