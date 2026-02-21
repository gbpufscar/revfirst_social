"""FastAPI application entrypoint for RevFirst_Social foundation."""

from __future__ import annotations

from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from src.billing.webhooks import router as billing_router
from src.auth.middleware import AUTH_CONTEXT_KEY, resolve_request_auth_context
from src.auth.router import router as auth_router
from src.control.security import get_telegram_notification_channel_status
from src.control.telegram_bot import router as control_telegram_router
from src.core.config import get_settings
from src.core.logger import bind_request_context, clear_request_context, get_logger
from src.core.metrics import record_http_request, record_rate_limit_block, render_prometheus_metrics
from src.core.observability import init_sentry, sentry_scope
from src.core.rate_limit import RateLimitDecision, get_ip_rate_limiter
from src.daily_post.router import router as daily_post_router
from src.ingestion.router import router as ingestion_router
from src.integrations.telegram.router import router as telegram_integration_router
from src.integrations.x.router import router as x_integration_router
from src.media.router import router as media_router
from src.publishing.router import router as publishing_router
from src.storage.db import load_models
from src.storage.db import test_connection as test_db_connection
from src.storage.redis_client import test_connection as test_redis_connection
from src.workspaces.router import router as workspaces_router


settings = get_settings()
logger = get_logger("revfirst.api")

app = FastAPI(title=settings.app_name, version=settings.app_version)


def _resolve_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        first = forwarded_for.split(",")[0].strip()
        if first:
            return first

    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()

    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _apply_rate_limit_headers(response: Response, decision: RateLimitDecision) -> None:
    response.headers["x-rate-limit-limit"] = str(decision.limit)
    response.headers["x-rate-limit-remaining"] = str(decision.remaining)
    response.headers["x-rate-limit-reset"] = str(decision.reset_seconds)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    started_at = perf_counter()
    request_id = request.headers.get("x-request-id", str(uuid4()))
    auth_context = resolve_request_auth_context(request)
    setattr(request.state, AUTH_CONTEXT_KEY, auth_context)

    workspace_id = request.headers.get("x-workspace-id")
    if workspace_id is None and auth_context is not None:
        workspace_id = auth_context.workspace_id
    bind_request_context(request_id=request_id, workspace_id=workspace_id)

    response = None
    decision = None
    status_code = 500

    try:
        with sentry_scope(workspace_id=workspace_id, request_id=request_id):
            if settings.ip_rate_limit_enabled and settings.env.lower() in {"prod", "production"}:
                limiter = get_ip_rate_limiter()
                decision = limiter.check(ip=_resolve_client_ip(request))
                if not decision.allowed:
                    record_rate_limit_block(kind="ip")
                    response = JSONResponse(
                        status_code=429,
                        content={
                            "detail": "Rate limit exceeded",
                            "limit": decision.limit,
                            "remaining": decision.remaining,
                            "reset_seconds": decision.reset_seconds,
                        },
                    )
                else:
                    response = await call_next(request)
            else:
                response = await call_next(request)
        status_code = int(response.status_code)
    finally:
        duration = perf_counter() - started_at
        if settings.metrics_enabled:
            record_http_request(
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                duration_seconds=duration,
            )
        clear_request_context()

    if decision is not None:
        _apply_rate_limit_headers(response, decision)
    response.headers["x-request-id"] = request_id
    return response


@app.on_event("startup")
def on_startup() -> None:
    load_models()
    sentry_enabled = init_sentry()
    telegram_channel = get_telegram_notification_channel_status()
    logger.info(
        "application_startup",
        env=settings.env,
        version=settings.app_version,
        sentry_enabled=sentry_enabled,
        metrics_enabled=settings.metrics_enabled,
        ip_rate_limit_enabled=settings.ip_rate_limit_enabled,
        telegram_notification_status=telegram_channel.status,
    )
    if telegram_channel.degraded:
        logger.warning(
            "telegram_notification_channel_degraded",
            reasons=sorted(telegram_channel.reasons),
            has_bot_token=telegram_channel.has_bot_token,
            allowed_telegram_ids_count=telegram_channel.allowed_ids_count,
        )


@app.get("/health")
def health() -> JSONResponse:
    db_ok, db_error = test_db_connection()
    redis_ok, redis_error = test_redis_connection()

    healthy = db_ok and redis_ok
    status = "ok" if healthy else "degraded"

    payload = {
        "status": status,
        "env": settings.env,
        "services": {
            "database": {"ok": db_ok, "error": db_error},
            "redis": {"ok": redis_ok, "error": redis_error},
        },
    }

    return JSONResponse(content=payload, status_code=200 if healthy else 503)


@app.get("/version")
def version() -> dict[str, str]:
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "env": settings.env,
    }


@app.get("/metrics")
def metrics() -> PlainTextResponse:
    if not settings.metrics_enabled:
        return PlainTextResponse("metrics disabled\n", status_code=404)

    payload = render_prometheus_metrics(
        app_name=settings.app_name,
        app_version=settings.app_version,
        env=settings.env,
    )
    return PlainTextResponse(
        payload,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


app.include_router(auth_router)
app.include_router(workspaces_router)
app.include_router(billing_router)
app.include_router(x_integration_router)
app.include_router(telegram_integration_router)
app.include_router(control_telegram_router)
app.include_router(ingestion_router)
app.include_router(publishing_router)
app.include_router(daily_post_router)
app.include_router(media_router)
