"""Pydantic schemas for workspace management API."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    owner_email: EmailStr
    owner_password: str = Field(min_length=8, max_length=255)


class WorkspaceCreateResponse(BaseModel):
    workspace_id: str
    name: str
    owner_user_id: str
    owner_role: str


class WorkspaceResponse(BaseModel):
    id: str
    name: str
    plan: str
    subscription_status: str
    created_at: str
    my_role: str
