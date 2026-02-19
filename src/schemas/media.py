"""Schemas for media infrastructure endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class MediaGenerateRequest(BaseModel):
    workspace_id: str = Field(min_length=36, max_length=36)
    channel: str = Field(min_length=1, max_length=24)
    content_text: str = Field(min_length=5)
    source_kind: Optional[str] = Field(default=None, max_length=40)
    source_ref_id: Optional[str] = Field(default=None, max_length=64)
    idempotency_key: Optional[str] = Field(default=None, max_length=80)


class MediaGenerateResponse(BaseModel):
    workspace_id: str
    channel: str
    status: str
    message: str
    success: bool
    job_id: Optional[str] = None
    asset_id: Optional[str] = None
    public_url: Optional[str] = None
    reused: bool = False


class MediaAssetItem(BaseModel):
    id: str
    workspace_id: str
    channel: str
    provider: str
    purpose: Optional[str]
    mime_type: str
    width: Optional[int]
    height: Optional[int]
    public_url: str
    created_at: datetime


class MediaAssetListResponse(BaseModel):
    workspace_id: str
    items: list[MediaAssetItem]
