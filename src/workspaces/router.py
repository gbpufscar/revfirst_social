"""Workspace management API routes."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.auth.dependencies import require_workspace_role
from src.auth.jwt import AuthContext
from src.schemas.workspace import WorkspaceCreateRequest, WorkspaceCreateResponse, WorkspaceResponse
from src.storage.db import get_session
from src.storage.tenant import set_workspace_context
from src.workspaces.service import create_workspace_with_owner, get_workspace_for_member


router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.post("", response_model=WorkspaceCreateResponse, status_code=201)
def create_workspace(
    payload: WorkspaceCreateRequest,
    session: Session = Depends(get_session),
) -> WorkspaceCreateResponse:
    workspace, user, role_name = create_workspace_with_owner(
        session,
        workspace_name=payload.name,
        owner_email=payload.owner_email,
        owner_password=payload.owner_password,
    )
    return WorkspaceCreateResponse(
        workspace_id=workspace.id,
        name=workspace.name,
        owner_user_id=user.id,
        owner_role=role_name,
    )


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
def get_workspace(
    workspace_id: str,
    auth: AuthContext = Depends(require_workspace_role("owner", "admin", "member")),
    session: Session = Depends(get_session),
) -> WorkspaceResponse:
    set_workspace_context(session, workspace_id)
    workspace, role_name = get_workspace_for_member(
        session=session,
        workspace_id=workspace_id,
        user_id=auth.user_id,
    )
    return WorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        plan=workspace.plan,
        subscription_status=workspace.subscription_status,
        created_at=workspace.created_at.isoformat() if isinstance(workspace.created_at, datetime) else str(workspace.created_at),
        my_role=role_name,
    )
