"""Pydantic schemas for X integration endpoints."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class XOAuthExchangeRequest(BaseModel):
    workspace_id: str = Field(min_length=36, max_length=36)
    authorization_code: str = Field(min_length=6, max_length=4096)
    code_verifier: Optional[str] = Field(default=None, min_length=16, max_length=255)


class XManualTokenRequest(BaseModel):
    workspace_id: str = Field(min_length=36, max_length=36)
    access_token: str = Field(min_length=10, max_length=8192)
    refresh_token: Optional[str] = Field(default=None, min_length=10, max_length=8192)
    expires_in: Optional[int] = Field(default=None, ge=1, le=31536000)
    scope: Optional[str] = Field(default=None, max_length=255)
    token_type: str = Field(default="bearer", max_length=32)


class XConnectionStatusResponse(BaseModel):
    workspace_id: str
    connected: bool
    token_type: Optional[str] = None
    scope: Optional[str] = None
    expires_at: Optional[str] = None
    updated_at: Optional[str] = None
    has_refresh_token: bool = False


class XOAuthExchangeResponse(BaseModel):
    workspace_id: str
    connected: bool
    expires_at: Optional[str] = None
    token_type: str
    scope: Optional[str] = None

