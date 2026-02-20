"""Pydantic schemas for X integration endpoints."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class XOAuthAuthorizeRequest(BaseModel):
    workspace_id: str = Field(min_length=36, max_length=36)


class XOAuthAuthorizeResponse(BaseModel):
    workspace_id: str
    authorize_url: str
    state: str
    expires_in: int = Field(ge=1)
    scope: str


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
    connected_reason: Optional[str] = None
    token_type: Optional[str] = None
    scope: Optional[str] = None
    expires_at: Optional[str] = None
    updated_at: Optional[str] = None
    has_refresh_token: bool = False
    access_token_valid: bool = False
    is_expired: bool = False
    can_auto_refresh: bool = False
    account_user_id: Optional[str] = None
    account_username: Optional[str] = None
    has_publish_scope: bool = False
    publish_ready: bool = False


class XOAuthExchangeResponse(BaseModel):
    workspace_id: str
    connected: bool
    expires_at: Optional[str] = None
    token_type: str
    scope: Optional[str] = None
    account_user_id: Optional[str] = None
    account_username: Optional[str] = None
    has_publish_scope: bool = False
