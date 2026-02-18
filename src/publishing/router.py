"""Publishing API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.auth.dependencies import require_workspace_role
from src.auth.jwt import AuthContext
from src.core.config import get_settings
from src.integrations.x.x_client import XClient, get_x_client
from src.publishing.service import publish_post, publish_reply
from src.schemas.publishing import PublishPostRequest, PublishReplyRequest, PublishResponse
from src.storage.db import get_session
from src.storage.tenant import set_workspace_context


router = APIRouter(prefix="/publishing", tags=["publishing"])


def _enforce_workspace_scope(auth: AuthContext, workspace_id: str) -> None:
    if auth.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token workspace scope mismatch")


def _validate_text_size(text: str) -> None:
    settings = get_settings()
    if len(text) > settings.publish_max_text_chars:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Text exceeds publish_max_text_chars={settings.publish_max_text_chars}",
        )


@router.post("/reply", response_model=PublishResponse)
def publish_reply_endpoint(
    payload: PublishReplyRequest,
    auth: AuthContext = Depends(require_workspace_role("owner", "admin", "member")),
    session: Session = Depends(get_session),
    x_client: XClient = Depends(get_x_client),
) -> PublishResponse:
    _enforce_workspace_scope(auth, payload.workspace_id)
    _validate_text_size(payload.text)
    set_workspace_context(session, payload.workspace_id)
    result = publish_reply(
        session,
        workspace_id=payload.workspace_id,
        text=payload.text,
        in_reply_to_tweet_id=payload.in_reply_to_tweet_id,
        thread_id=payload.thread_id,
        target_author_id=payload.target_author_id,
        x_client=x_client,
    )
    if not result.published and result.status in {"blocked_plan", "blocked_cooldown"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=result.message,
        )
    if not result.published and result.status == "failed":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=result.message,
        )
    return PublishResponse(
        workspace_id=result.workspace_id,
        action=result.action,
        published=result.published,
        external_post_id=result.external_post_id,
        status=result.status,
        message=result.message,
    )


@router.post("/post", response_model=PublishResponse)
def publish_post_endpoint(
    payload: PublishPostRequest,
    auth: AuthContext = Depends(require_workspace_role("owner", "admin", "member")),
    session: Session = Depends(get_session),
    x_client: XClient = Depends(get_x_client),
) -> PublishResponse:
    _enforce_workspace_scope(auth, payload.workspace_id)
    _validate_text_size(payload.text)
    set_workspace_context(session, payload.workspace_id)
    result = publish_post(
        session,
        workspace_id=payload.workspace_id,
        text=payload.text,
        x_client=x_client,
    )
    if not result.published and result.status == "blocked_plan":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=result.message,
        )
    if not result.published and result.status == "failed":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=result.message,
        )
    return PublishResponse(
        workspace_id=result.workspace_id,
        action=result.action,
        published=result.published,
        external_post_id=result.external_post_id,
        status=result.status,
        message=result.message,
    )

