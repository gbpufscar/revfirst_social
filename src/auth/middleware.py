"""Authentication middleware helpers."""

from __future__ import annotations

from typing import Optional

from fastapi import Request

from src.auth.jwt import AuthContext, decode_access_token


AUTH_CONTEXT_KEY = "auth_context"


def _extract_bearer_token(request: Request) -> Optional[str]:
    authorization = request.headers.get("authorization")
    if not authorization:
        return None

    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def resolve_request_auth_context(request: Request) -> Optional[AuthContext]:
    token = _extract_bearer_token(request)
    if not token:
        return None

    try:
        return decode_access_token(token)
    except Exception:
        return None
