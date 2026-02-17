"""Workspace-scoped X OAuth token storage service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.storage.models import XOAuthToken
from src.storage.security import decrypt_token, encrypt_token, hash_token


def _resolve_expiration(expires_in: Optional[int]) -> Optional[datetime]:
    if expires_in is None:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=expires_in)


def upsert_workspace_x_tokens(
    session: Session,
    *,
    workspace_id: str,
    access_token: str,
    refresh_token: Optional[str] = None,
    token_type: str = "bearer",
    scope: Optional[str] = None,
    expires_in: Optional[int] = None,
) -> XOAuthToken:
    expires_at = _resolve_expiration(expires_in)
    record = session.scalar(
        select(XOAuthToken).where(
            XOAuthToken.workspace_id == workspace_id,
            XOAuthToken.provider == "x",
        )
    )
    if record is None:
        record = XOAuthToken(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            provider="x",
            access_token_hash=hash_token(access_token),
            access_token_encrypted=encrypt_token(access_token),
            refresh_token_hash=hash_token(refresh_token) if refresh_token else None,
            refresh_token_encrypted=encrypt_token(refresh_token) if refresh_token else None,
            token_type=token_type or "bearer",
            scope=scope,
            expires_at=expires_at,
            revoked_at=None,
        )
        session.add(record)
    else:
        record.access_token_hash = hash_token(access_token)
        record.access_token_encrypted = encrypt_token(access_token)
        record.refresh_token_hash = hash_token(refresh_token) if refresh_token else None
        record.refresh_token_encrypted = encrypt_token(refresh_token) if refresh_token else None
        record.token_type = token_type or "bearer"
        record.scope = scope
        record.expires_at = expires_at
        record.revoked_at = None
        record.updated_at = datetime.now(timezone.utc)

    session.commit()
    return record


def get_workspace_x_access_token(session: Session, *, workspace_id: str) -> Optional[str]:
    record = session.scalar(
        select(XOAuthToken).where(
            XOAuthToken.workspace_id == workspace_id,
            XOAuthToken.provider == "x",
            XOAuthToken.revoked_at.is_(None),
        )
    )
    if record is None:
        return None
    expires_at = record.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at is not None and expires_at < datetime.now(timezone.utc):
        return None
    return decrypt_token(record.access_token_encrypted)


def get_workspace_x_connection_status(session: Session, *, workspace_id: str) -> Dict[str, Any]:
    record = session.scalar(
        select(XOAuthToken).where(
            XOAuthToken.workspace_id == workspace_id,
            XOAuthToken.provider == "x",
        )
    )
    if record is None:
        return {
            "workspace_id": workspace_id,
            "connected": False,
            "token_type": None,
            "scope": None,
            "expires_at": None,
            "updated_at": None,
            "has_refresh_token": False,
        }

    return {
        "workspace_id": workspace_id,
        "connected": record.revoked_at is None,
        "token_type": record.token_type,
        "scope": record.scope,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        "has_refresh_token": bool(record.refresh_token_encrypted),
    }


def revoke_workspace_x_tokens(session: Session, *, workspace_id: str) -> bool:
    record = session.scalar(
        select(XOAuthToken).where(
            XOAuthToken.workspace_id == workspace_id,
            XOAuthToken.provider == "x",
            XOAuthToken.revoked_at.is_(None),
        )
    )
    if record is None:
        return False
    record.revoked_at = datetime.now(timezone.utc)
    record.updated_at = datetime.now(timezone.utc)
    session.commit()
    return True
