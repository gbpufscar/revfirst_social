"""Pydantic schemas for billing endpoints."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class StripeWebhookResponse(BaseModel):
    status: str
    duplicate: bool
    event_id: str
    event_type: str
    message: Optional[str] = None
