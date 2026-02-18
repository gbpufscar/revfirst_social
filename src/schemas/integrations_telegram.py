"""Pydantic schemas for Telegram seed ingestion."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class TelegramManualSeedRequest(BaseModel):
    workspace_id: str = Field(min_length=36, max_length=36)
    text: str = Field(min_length=3, max_length=1200)
    source_chat_id: str = Field(default="manual", min_length=1, max_length=64)
    source_message_id: Optional[str] = Field(default=None, max_length=64)
    source_user_id: Optional[str] = Field(default=None, max_length=64)


class TelegramSeedResponse(BaseModel):
    seed_id: str
    workspace_id: str
    source_chat_id: str
    source_message_id: str
    source_user_id: Optional[str] = None
    text: str
    style_fingerprint: Dict[str, Any]
    created_at: datetime


class TelegramWebhookResponse(BaseModel):
    accepted: bool
    workspace_id: str
    seed_id: Optional[str] = None
    reason: Optional[str] = None

