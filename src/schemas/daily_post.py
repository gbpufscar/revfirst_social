"""Pydantic schemas for daily post generation."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DailyPostGenerateRequest(BaseModel):
    workspace_id: str = Field(min_length=36, max_length=36)
    topic: Optional[str] = Field(default=None, min_length=3, max_length=120)
    auto_publish: Optional[bool] = None


class DailyPostGenerateResponse(BaseModel):
    workspace_id: str
    draft_id: str
    status: str
    text: str
    brand_passed: bool
    brand_score: int = Field(ge=0, le=100)
    cringe_passed: bool
    cringe_risk_score: int = Field(ge=0, le=100)
    published: bool
    external_post_id: Optional[str] = None
    seed_count: int = Field(ge=0)
    message: str
    content_object: Dict[str, Any] = Field(default_factory=dict)
    channel_targets: List[str] = Field(default_factory=list)
    blocked_channels: Dict[str, str] = Field(default_factory=dict)
    channel_previews: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


class DailyPostDraftItem(BaseModel):
    draft_id: str
    workspace_id: str
    topic: Optional[str] = None
    status: str
    text: str
    brand_score: int = Field(ge=0, le=100)
    cringe_risk_score: int = Field(ge=0, le=100)
    external_post_id: Optional[str] = None
    created_at: datetime


class DailyPostDraftListResponse(BaseModel):
    workspace_id: str
    items: List[DailyPostDraftItem]
