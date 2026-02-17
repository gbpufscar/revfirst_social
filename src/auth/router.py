"""Authentication API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.auth.jwt import AuthContext, create_access_token
from src.schemas.auth import LoginRequest, TokenResponse
from src.storage.db import get_session
from src.storage.tenant import set_workspace_context
from src.workspaces.service import authenticate_workspace_user


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, session: Session = Depends(get_session)) -> TokenResponse:
    set_workspace_context(session, payload.workspace_id)
    user, _membership, role_name = authenticate_workspace_user(
        session,
        email=payload.email,
        password=payload.password,
        workspace_id=payload.workspace_id,
    )

    token, expires_in = create_access_token(
        AuthContext(
            user_id=user.id,
            workspace_id=payload.workspace_id,
            role=role_name,
            email=user.email,
        )
    )

    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        workspace_id=payload.workspace_id,
        role=role_name,
    )
