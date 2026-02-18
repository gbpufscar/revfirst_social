"""FastAPI application entrypoint for RevFirst_Social foundation."""

from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.billing.webhooks import router as billing_router
from src.auth.middleware import AUTH_CONTEXT_KEY, resolve_request_auth_context
from src.auth.router import router as auth_router
from src.core.config import get_settings
from src.core.logger import bind_request_context, clear_request_context, get_logger
from src.ingestion.router import router as ingestion_router
from src.integrations.x.router import router as x_integration_router
from src.publishing.router import router as publishing_router
from src.storage.db import load_models
from src.storage.db import test_connection as test_db_connection
from src.storage.redis_client import test_connection as test_redis_connection
from src.workspaces.router import router as workspaces_router


settings = get_settings()
logger = get_logger("revfirst.api")

app = FastAPI(title=settings.app_name, version=settings.app_version)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id", str(uuid4()))
    auth_context = resolve_request_auth_context(request)
    setattr(request.state, AUTH_CONTEXT_KEY, auth_context)

    workspace_id = request.headers.get("x-workspace-id")
    if workspace_id is None and auth_context is not None:
        workspace_id = auth_context.workspace_id
    bind_request_context(request_id=request_id, workspace_id=workspace_id)

    try:
        response = await call_next(request)
    finally:
        clear_request_context()

    response.headers["x-request-id"] = request_id
    return response


@app.on_event("startup")
def on_startup() -> None:
    load_models()
    logger.info("application_startup", env=settings.env, version=settings.app_version)


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


app.include_router(auth_router)
app.include_router(workspaces_router)
app.include_router(billing_router)
app.include_router(x_integration_router)
app.include_router(ingestion_router)
app.include_router(publishing_router)
