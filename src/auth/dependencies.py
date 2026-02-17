"""FastAPI dependencies for auth and role enforcement."""

from __future__ import annotations

from collections.abc import Callable
from typing import Optional

from fastapi import Depends, HTTPException, Request, status

from src.auth.jwt import AuthContext
from src.auth.middleware import AUTH_CONTEXT_KEY


def get_optional_auth_context(request: Request) -> Optional[AuthContext]:
    return getattr(request.state, AUTH_CONTEXT_KEY, None)


def require_auth_context(auth: Optional[AuthContext] = Depends(get_optional_auth_context)) -> AuthContext:
    if auth is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return auth


def require_workspace_role(*allowed_roles: str) -> Callable[[AuthContext], AuthContext]:
    allowed = set(allowed_roles)

    def dependency(auth: AuthContext = Depends(require_auth_context)) -> AuthContext:
        if auth.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role",
            )
        return auth

    return dependency
