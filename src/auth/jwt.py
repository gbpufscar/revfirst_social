"""JWT issue/verify primitives for workspace-scoped authentication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import HTTPException, status

from src.core.config import get_settings


@dataclass(frozen=True)
class AuthContext:
    user_id: str
    workspace_id: str
    role: str
    email: str


def create_access_token(context: AuthContext) -> tuple[str, int]:
    settings = get_settings()
    expires_in = settings.access_token_exp_minutes * 60
    now = datetime.now(timezone.utc)
    payload = {
        "sub": context.user_id,
        "email": context.email,
        "workspace_id": context.workspace_id,
        "role": context.role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    return token, expires_in


def decode_access_token(token: str) -> AuthContext:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
        return AuthContext(
            user_id=str(payload["sub"]),
            workspace_id=str(payload["workspace_id"]),
            role=str(payload["role"]),
            email=str(payload.get("email", "")),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc
