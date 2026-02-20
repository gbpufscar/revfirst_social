"""Daily post routes powered by Telegram style seeds."""

from __future__ import annotations

import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from src.auth.dependencies import require_workspace_role
from src.auth.jwt import AuthContext
from src.core.config import get_settings
from src.daily_post.service import generate_daily_post, list_daily_post_drafts
from src.integrations.x.x_client import XClient, get_x_client
from src.schemas.daily_post import (
    DailyPostDraftItem,
    DailyPostDraftListResponse,
    DailyPostGenerateRequest,
    DailyPostGenerateResponse,
)
from src.storage.db import get_session
from src.storage.tenant import set_workspace_context


router = APIRouter(prefix="/daily-post", tags=["daily-post"])


def _enforce_workspace_scope(auth: AuthContext, workspace_id: str) -> None:
    if auth.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token workspace scope mismatch")


def _enforce_auto_publish_guard(auth: AuthContext, internal_publish_key: Optional[str]) -> None:
    if auth.role not in {"owner", "admin"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")

    settings = get_settings()
    if not settings.publishing_direct_api_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="direct_publish_api_disabled",
        )

    expected = settings.publishing_direct_api_internal_key.strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="direct_publish_api_misconfigured",
        )

    received = (internal_publish_key or "").strip()
    if not received or not secrets.compare_digest(received, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid_internal_publish_key",
        )


@router.post("/generate", response_model=DailyPostGenerateResponse)
def generate_daily_post_endpoint(
    payload: DailyPostGenerateRequest,
    auth: AuthContext = Depends(require_workspace_role("owner", "admin", "member")),
    session: Session = Depends(get_session),
    x_client: XClient = Depends(get_x_client),
    internal_publish_key: Optional[str] = Header(default=None, alias="X-RevFirst-Internal-Key"),
) -> DailyPostGenerateResponse:
    _enforce_workspace_scope(auth, payload.workspace_id)
    set_workspace_context(session, payload.workspace_id)

    settings = get_settings()
    auto_publish = payload.auto_publish
    if auto_publish is None:
        auto_publish = settings.daily_post_auto_publish_default
    if auto_publish:
        _enforce_auto_publish_guard(auth, internal_publish_key)

    result = generate_daily_post(
        session,
        workspace_id=payload.workspace_id,
        topic=payload.topic,
        auto_publish=auto_publish,
        x_client=x_client,
    )
    return DailyPostGenerateResponse(
        workspace_id=result.workspace_id,
        draft_id=result.draft_id,
        status=result.status,
        text=result.text,
        brand_passed=result.brand_passed,
        brand_score=result.brand_score,
        cringe_passed=result.cringe_passed,
        cringe_risk_score=result.cringe_risk_score,
        published=result.published,
        external_post_id=result.external_post_id,
        seed_count=result.seed_count,
        message=result.message,
        content_object=result.content_object,
        channel_targets=result.channel_targets,
        blocked_channels=result.blocked_channels,
        channel_previews=result.channel_previews,
    )


@router.get("/drafts/{workspace_id}", response_model=DailyPostDraftListResponse)
def list_daily_posts_endpoint(
    workspace_id: str,
    limit: int = 20,
    auth: AuthContext = Depends(require_workspace_role("owner", "admin", "member")),
    session: Session = Depends(get_session),
) -> DailyPostDraftListResponse:
    _enforce_workspace_scope(auth, workspace_id)
    set_workspace_context(session, workspace_id)

    drafts = list_daily_post_drafts(
        session,
        workspace_id=workspace_id,
        limit=limit,
    )
    return DailyPostDraftListResponse(
        workspace_id=workspace_id,
        items=[
            DailyPostDraftItem(
                draft_id=item.id,
                workspace_id=item.workspace_id,
                topic=item.topic,
                status=item.status,
                text=item.content_text,
                brand_score=item.brand_score,
                cringe_risk_score=item.cringe_risk_score,
                external_post_id=item.external_post_id,
                created_at=item.created_at,
            )
            for item in drafts
        ],
    )
