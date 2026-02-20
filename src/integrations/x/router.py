"""X integration API routes (OAuth by workspace)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from src.auth.dependencies import require_workspace_role
from src.auth.jwt import AuthContext
from src.core.config import get_settings
from src.integrations.x.service import (
    begin_workspace_x_oauth_authorization,
    complete_workspace_x_oauth_callback,
    get_workspace_x_connection_status,
    revoke_workspace_x_tokens,
    upsert_workspace_x_tokens,
)
from src.integrations.x.x_client import XClient, XClientError, get_x_client
from src.schemas.integrations_x import (
    XOAuthAuthorizeRequest,
    XOAuthAuthorizeResponse,
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


def _scope_has_publish_permission(scope: str | None) -> bool:
    required = get_settings().x_required_publish_scope.strip()
    values = {part.strip() for part in str(scope or "").split(" ") if part.strip()}
    return required in values


@router.post("/oauth/authorize", response_model=XOAuthAuthorizeResponse)
def oauth_authorize(
    payload: XOAuthAuthorizeRequest,
    auth: AuthContext = Depends(require_workspace_role("owner", "admin")),
) -> XOAuthAuthorizeResponse:
    _enforce_workspace_scope(auth, payload.workspace_id)
    try:
        authorize_payload = begin_workspace_x_oauth_authorization(workspace_id=payload.workspace_id)
    except XClientError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return XOAuthAuthorizeResponse(**authorize_payload)


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
    normalized_scope = str(scope).strip() if isinstance(scope, str) else None
    if not _scope_has_publish_permission(normalized_scope):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"X OAuth scope missing required permission: {get_settings().x_required_publish_scope}",
        )
    try:
        account = x_client.get_authenticated_user(access_token=access_token)
    except XClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    record = upsert_workspace_x_tokens(
        session,
        workspace_id=payload.workspace_id,
        access_token=access_token,
        refresh_token=str(refresh_token) if isinstance(refresh_token, str) else None,
        token_type=token_type,
        scope=normalized_scope,
        expires_in=int(expires_in) if isinstance(expires_in, int) else None,
        account_user_id=str(account.get("id") or "").strip() or None,
        account_username=str(account.get("username") or "").strip() or None,
    )
    return XOAuthExchangeResponse(
        workspace_id=payload.workspace_id,
        connected=True,
        expires_at=record.expires_at.isoformat() if record.expires_at else None,
        token_type=record.token_type,
        scope=record.scope,
        account_user_id=record.account_user_id,
        account_username=record.account_username,
        has_publish_scope=_scope_has_publish_permission(record.scope),
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
        account_user_id=record.account_user_id,
        account_username=record.account_username,
        has_publish_scope=_scope_has_publish_permission(record.scope),
    )


@router.get("/oauth/callback", response_model=XOAuthExchangeResponse)
def oauth_callback(
    code: str = Query(min_length=3, max_length=4096),
    state: str = Query(min_length=10, max_length=512),
    error: Optional[str] = Query(default=None),
    error_description: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
    x_client: XClient = Depends(get_x_client),
) -> XOAuthExchangeResponse:
    if error:
        message = error_description or error
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"X OAuth callback error: {message}")

    try:
        payload = complete_workspace_x_oauth_callback(
            session,
            authorization_code=code,
            state=state,
            x_client=x_client,
        )
    except XClientError as exc:
        message = str(exc)
        status_code = status.HTTP_400_BAD_REQUEST if "state" in message.lower() or "scope" in message.lower() else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=status_code, detail=message) from exc

    return XOAuthExchangeResponse(
        workspace_id=str(payload["workspace_id"]),
        connected=bool(payload["connected"]),
        expires_at=str(payload["expires_at"]) if payload.get("expires_at") else None,
        token_type=str(payload["token_type"]),
        scope=str(payload["scope"]) if payload.get("scope") else None,
        account_user_id=str(payload["account_user_id"]) if payload.get("account_user_id") else None,
        account_username=str(payload["account_username"]) if payload.get("account_username") else None,
        has_publish_scope=bool(payload.get("has_publish_scope")),
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
