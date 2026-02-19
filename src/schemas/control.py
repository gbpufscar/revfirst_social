"""Schemas for control-plane webhook responses."""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ControlWebhookResponse(BaseModel):
    accepted: bool
    workspace_id: str
    request_id: str
    command: Optional[str] = None
    status: str
    message: str
    data: Dict[str, Any] = Field(default_factory=dict)
