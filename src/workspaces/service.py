"""Workspace and membership application services."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.storage.models import Role, User, Workspace, WorkspaceUser
from src.storage.security import hash_password, verify_password


DEFAULT_ROLES = ("owner", "admin", "member")


def ensure_default_roles(session: Session) -> None:
    existing = set(session.scalars(select(Role.name)).all())
    missing = [Role(name=name) for name in DEFAULT_ROLES if name not in existing]
    if missing:
        session.add_all(missing)
        session.commit()


def create_workspace_with_owner(
    session: Session,
    *,
    workspace_name: str,
    owner_email: str,
    owner_password: str,
) -> tuple[Workspace, User, str]:
    existing_workspace = session.scalar(select(Workspace).where(Workspace.name == workspace_name))
    if existing_workspace is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Workspace name already exists",
        )

    ensure_default_roles(session)
    owner_role = session.scalar(select(Role).where(Role.name == "owner"))
    if owner_role is None:  # pragma: no cover
        raise RuntimeError("Owner role was not initialized")

    user = session.scalar(select(User).where(User.email == owner_email))
    if user is None:
        user = User(id=str(uuid.uuid4()), email=owner_email, password_hash=hash_password(owner_password))
        session.add(user)
        session.flush()
    elif not verify_password(owner_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Owner email already exists with different credentials",
        )

    workspace = Workspace(
        id=str(uuid.uuid4()),
        name=workspace_name,
        plan="free",
        subscription_status="inactive",
    )
    session.add(workspace)
    session.flush()

    membership = WorkspaceUser(
        id=str(uuid.uuid4()),
        workspace_id=workspace.id,
        user_id=user.id,
        role_id=owner_role.id,
    )
    session.add(membership)
    session.commit()

    return workspace, user, owner_role.name


def authenticate_workspace_user(
    session: Session,
    *,
    email: str,
    password: str,
    workspace_id: str,
) -> tuple[User, WorkspaceUser, str]:
    user = session.scalar(select(User).where(User.email == email, User.is_active.is_(True)))
    if user is None or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    membership = session.scalar(
        select(WorkspaceUser)
        .where(WorkspaceUser.user_id == user.id, WorkspaceUser.workspace_id == workspace_id)
    )
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not a member of this workspace",
        )

    role_name = session.scalar(select(Role.name).where(Role.id == membership.role_id))
    if role_name is None:  # pragma: no cover
        raise RuntimeError("Role lookup failed")

    return user, membership, role_name


def get_workspace_for_member(session: Session, workspace_id: str, user_id: str) -> tuple[Workspace, str]:
    workspace = session.scalar(select(Workspace).where(Workspace.id == workspace_id))
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

    membership = session.scalar(
        select(WorkspaceUser)
        .where(WorkspaceUser.workspace_id == workspace_id, WorkspaceUser.user_id == user_id)
    )
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not allowed to access this workspace",
        )

    role_name = session.scalar(select(Role.name).where(Role.id == membership.role_id))
    if role_name is None:  # pragma: no cover
        raise RuntimeError("Role lookup failed")

    return workspace, role_name
