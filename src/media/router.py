"""Media infrastructure API routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from src.auth.dependencies import require_workspace_role
from src.auth.jwt import AuthContext
from src.media.service import generate_image_asset, get_media_asset, list_media_assets, resolve_media_file_path
from src.schemas.media import (
    MediaAssetItem,
    MediaAssetListResponse,
    MediaGenerateRequest,
    MediaGenerateResponse,
)
from src.storage.db import get_session
from src.storage.tenant import set_workspace_context


router = APIRouter(prefix="/media", tags=["media"])


def _enforce_workspace_scope(auth: AuthContext, workspace_id: str) -> None:
    if auth.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token workspace scope mismatch")


@router.post("/generate", response_model=MediaGenerateResponse)
def generate_media(
    payload: MediaGenerateRequest,
    auth: AuthContext = Depends(require_workspace_role("owner", "admin", "member")),
    session: Session = Depends(get_session),
) -> MediaGenerateResponse:
    _enforce_workspace_scope(auth, payload.workspace_id)
    set_workspace_context(session, payload.workspace_id)

    result = generate_image_asset(
        session,
        workspace_id=payload.workspace_id,
        channel=payload.channel,
        content_text=payload.content_text,
        source_kind=payload.source_kind,
        source_ref_id=payload.source_ref_id,
        idempotency_key=payload.idempotency_key,
        requested_by_user_id=auth.user_id,
    )
    return MediaGenerateResponse(
        workspace_id=result.workspace_id,
        channel=result.channel,
        status=result.status,
        message=result.message,
        success=result.success,
        job_id=result.job_id,
        asset_id=result.asset_id,
        public_url=result.public_url,
        reused=result.reused,
    )


@router.get("/assets/{workspace_id}", response_model=MediaAssetListResponse)
def list_assets(
    workspace_id: str,
    limit: int = 20,
    auth: AuthContext = Depends(require_workspace_role("owner", "admin", "member")),
    session: Session = Depends(get_session),
) -> MediaAssetListResponse:
    _enforce_workspace_scope(auth, workspace_id)
    set_workspace_context(session, workspace_id)

    items = list_media_assets(session, workspace_id=workspace_id, limit=limit)
    return MediaAssetListResponse(
        workspace_id=workspace_id,
        items=[
            MediaAssetItem(
                id=item.id,
                workspace_id=item.workspace_id,
                channel=item.channel,
                provider=item.provider,
                purpose=item.purpose,
                mime_type=item.mime_type,
                width=item.width,
                height=item.height,
                public_url=item.public_url,
                created_at=item.created_at,
            )
            for item in items
        ],
    )


@router.get("/public/{asset_id}")
def public_asset(
    asset_id: str,
    session: Session = Depends(get_session),
):
    asset = get_media_asset(session, asset_id=asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="media_asset_not_found")

    if asset.storage_backend == "external_url":
        return RedirectResponse(url=asset.public_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    file_path = resolve_media_file_path(asset)
    if file_path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="media_file_unavailable")
    safe_path = Path(file_path)
    if not safe_path.exists() or not safe_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="media_file_not_found")

    return FileResponse(
        safe_path,
        media_type=asset.mime_type,
        filename=safe_path.name,
    )
