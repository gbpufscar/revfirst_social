"""Structured logging setup using structlog."""

from __future__ import annotations

import logging
from typing import Any

import structlog

from src.core.config import get_settings


_CONFIGURED = False


def _add_default_context(
    logger: Any,
    method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    del logger, method_name
    event_dict.setdefault("request_id", None)
    event_dict.setdefault("workspace_id", None)
    return event_dict


def configure_logging() -> None:
    """Initialize structlog once for JSON-formatted logs."""

    global _CONFIGURED
    if _CONFIGURED:
        return

    settings = get_settings()

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(message)s",
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _add_default_context,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )

    _CONFIGURED = True


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    configure_logging()
    return structlog.get_logger(name)


def bind_request_context(request_id: str, workspace_id: str | None = None) -> None:
    structlog.contextvars.bind_contextvars(
        request_id=request_id,
        workspace_id=workspace_id,
    )


def clear_request_context() -> None:
    structlog.contextvars.clear_contextvars()
