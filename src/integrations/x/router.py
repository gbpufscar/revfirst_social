"""X integration API routes (OAuth by workspace)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.auth.dependencies import require_workspace_role
from src.auth.jwt import AuthContext
from src.integrations.x.service import (
    get_workspace_x_connection_status,
    revoke_workspace_x_tokens,
    upsert_workspace_x_tokens,
)
from src.integrations.x.x_client import XClient, XClientError, get_x_client
from src.schemas.integrations_x import (
    XConnectionStatusResponse,
    XManualTokenRequest,
    XOAuthExchangeRequest,
    XOAuthExchangeResponse,
)
from src.storage.db import get_session
from src.storage.tenant import set_workspace_context


router = APIRouter(prefix="/integrations/x", tags=["integrations-x"])


def _enforce_workspace_scope(auth: AuthContext, workspace_id: str) -> None:
    if auth.workspace_id != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token workspace scope mismatch",
        )


@router.post("/oauth/exchange", response_model=XOAuthExchangeResponse)
def oauth_exchange(
    payload: XOAuthExchangeRequest,
    auth: AuthContext = Depends(require_workspace_role("owner", "admin")),
    session: Session = Depends(get_session),
    x_client: XClient = Depends(get_x_client),
) -> XOAuthExchangeResponse:
    _enforce_workspace_scope(auth, payload.workspace_id)
    set_workspace_context(session, payload.workspace_id)

    try:
        token_payload = x_client.exchange_code_for_tokens(
            authorization_code=payload.authorization_code,
            code_verifier=payload.code_verifier,
        )
    except XClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    token_type = str(token_payload.get("token_type") or "bearer")
    scope = token_payload.get("scope")
    expires_in = token_payload.get("expires_in")
    refresh_token = token_payload.get("refresh_token")
    access_token = token_payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="X token response missing access token")

    record = upsert_workspace_x_tokens(
        session,
        workspace_id=payload.workspace_id,
        access_token=access_token,
        refresh_token=str(refresh_token) if isinstance(refresh_token, str) else None,
        token_type=token_type,
        scope=str(scope) if isinstance(scope, str) else None,
        expires_in=int(expires_in) if isinstance(expires_in, int) else None,
    )
    return XOAuthExchangeResponse(
        workspace_id=payload.workspace_id,
        connected=True,
        expires_at=record.expires_at.isoformat() if record.expires_at else None,
        token_type=record.token_type,
        scope=record.scope,
    )


@router.post("/oauth/token/manual", response_model=XOAuthExchangeResponse)
def oauth_manual_token(
    payload: XManualTokenRequest,
    auth: AuthContext = Depends(require_workspace_role("owner", "admin")),
    session: Session = Depends(get_session),
) -> XOAuthExchangeResponse:
    _enforce_workspace_scope(auth, payload.workspace_id)
    set_workspace_context(session, payload.workspace_id)
    record = upsert_workspace_x_tokens(
        session,
        workspace_id=payload.workspace_id,
        access_token=payload.access_token,
        refresh_token=payload.refresh_token,
        token_type=payload.token_type,
        scope=payload.scope,
        expires_in=payload.expires_in,
    )
    return XOAuthExchangeResponse(
        workspace_id=payload.workspace_id,
        connected=True,
        expires_at=record.expires_at.isoformat() if record.expires_at else None,
        token_type=record.token_type,
        scope=record.scope,
    )


@router.get("/oauth/status/{workspace_id}", response_model=XConnectionStatusResponse)
def oauth_status(
    workspace_id: str,
    auth: AuthContext = Depends(require_workspace_role("owner", "admin", "member")),
    session: Session = Depends(get_session),
) -> XConnectionStatusResponse:
    _enforce_workspace_scope(auth, workspace_id)
    set_workspace_context(session, workspace_id)
    status_payload = get_workspace_x_connection_status(session, workspace_id=workspace_id)
    return XConnectionStatusResponse(**status_payload)


@router.post("/oauth/revoke/{workspace_id}")
def oauth_revoke(
    workspace_id: str,
    auth: AuthContext = Depends(require_workspace_role("owner", "admin")),
    session: Session = Depends(get_session),
) -> dict[str, bool]:
    _enforce_workspace_scope(auth, workspace_id)
    set_workspace_context(session, workspace_id)
    revoked = revoke_workspace_x_tokens(session, workspace_id=workspace_id)
    return {"revoked": revoked}

