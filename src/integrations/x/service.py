"""Workspace-scoped X OAuth token storage service."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
from typing import Any, Dict, Optional
from urllib.parse import urlencode
import uuid

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.config import get_settings
from src.core.metrics import record_x_token_refresh
from src.integrations.x.x_client import XClient, XClientError, get_x_client
from src.storage.models import XOAuthToken
from src.storage.redis_client import get_client as get_redis_client
from src.storage.security import decrypt_token, encrypt_token, hash_token
from src.storage.tenant import set_workspace_context


_REFRESH_LOCK_KEY_TEMPLATE = "revfirst:{workspace_id}:integrations:x:refresh_lock"
_OAUTH_STATE_KEY_TEMPLATE = "revfirst:integrations:x:oauth_state:{state}"
_RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
end
return 0
"""
_CONSUME_OAUTH_STATE_SCRIPT = """
local value = redis.call("get", KEYS[1])
if value then
  redis.call("del", KEYS[1])
end
return value
"""


@dataclass(frozen=True)
class _RefreshLock:
    redis_client: Optional[Redis]
    key: str
    token: str
    acquired: bool


def _refresh_lock_key(workspace_id: str) -> str:
    return _REFRESH_LOCK_KEY_TEMPLATE.format(workspace_id=workspace_id)


def _oauth_state_key(state: str) -> str:
    return _OAUTH_STATE_KEY_TEMPLATE.format(state=state)


def _safe_redis_client(redis_client: Optional[Redis]) -> Optional[Redis]:
    if redis_client is not None:
        return redis_client
    try:
        return get_redis_client()
    except Exception:
        return None


def _acquire_refresh_lock(
    redis_client: Optional[Redis],
    *,
    workspace_id: str,
    ttl_seconds: int,
) -> _RefreshLock:
    if redis_client is None:
        return _RefreshLock(redis_client=None, key="", token="", acquired=True)

    key = _refresh_lock_key(workspace_id)
    token = str(uuid.uuid4())
    try:
        acquired = bool(redis_client.set(key, token, nx=True, ex=max(1, ttl_seconds)))
    except Exception:
        return _RefreshLock(redis_client=None, key="", token="", acquired=True)

    return _RefreshLock(redis_client=redis_client, key=key, token=token, acquired=acquired)


def _release_refresh_lock(lock: _RefreshLock) -> None:
    if not lock.acquired or lock.redis_client is None:
        return
    try:
        lock.redis_client.eval(_RELEASE_LOCK_SCRIPT, 1, lock.key, lock.token)
    except Exception:
        return


def _normalize_expiration(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _coerce_expires_in(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        if value <= 0:
            return None
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            parsed = int(stripped)
        except ValueError:
            return None
        if parsed <= 0:
            return None
        return parsed
    return None


def _resolve_expiration(expires_in: Optional[int]) -> Optional[datetime]:
    if expires_in is None:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=expires_in)


def _normalize_scope(scope: Optional[str]) -> Optional[str]:
    if scope is None:
        return None
    normalized = " ".join(part for part in str(scope).split() if part.strip())
    return normalized or None


def _scope_values(scope: Optional[str]) -> set[str]:
    normalized = _normalize_scope(scope)
    if not normalized:
        return set()
    return {part.strip() for part in normalized.split(" ") if part.strip()}


def _has_required_publish_scope(scope: Optional[str]) -> bool:
    required = get_settings().x_required_publish_scope.strip()
    return required in _scope_values(scope)


def _pkce_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _select_active_token_record(session: Session, *, workspace_id: str) -> Optional[XOAuthToken]:
    return session.scalar(
        select(XOAuthToken).where(
            XOAuthToken.workspace_id == workspace_id,
            XOAuthToken.provider == "x",
            XOAuthToken.revoked_at.is_(None),
        )
    )


def _safe_decrypt_access_token(record: XOAuthToken) -> Optional[str]:
    try:
        token = decrypt_token(record.access_token_encrypted)
    except Exception:
        return None
    normalized = token.strip()
    return normalized or None


def _safe_decrypt_refresh_token(record: XOAuthToken) -> Optional[str]:
    if not record.refresh_token_encrypted:
        return None
    try:
        token = decrypt_token(record.refresh_token_encrypted)
    except Exception:
        return None
    normalized = token.strip()
    return normalized or None


def upsert_workspace_x_tokens(
    session: Session,
    *,
    workspace_id: str,
    access_token: str,
    refresh_token: Optional[str] = None,
    token_type: str = "bearer",
    scope: Optional[str] = None,
    expires_in: Optional[int] = None,
    account_user_id: Optional[str] = None,
    account_username: Optional[str] = None,
) -> XOAuthToken:
    expires_at = _resolve_expiration(expires_in)
    normalized_scope = _normalize_scope(scope)
    normalized_account_user_id = (
        account_user_id.strip() if isinstance(account_user_id, str) and account_user_id.strip() else None
    )
    normalized_account_username = (
        account_username.strip() if isinstance(account_username, str) and account_username.strip() else None
    )
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
            scope=normalized_scope,
            account_user_id=normalized_account_user_id,
            account_username=normalized_account_username,
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
        record.scope = normalized_scope
        if account_user_id is not None:
            record.account_user_id = normalized_account_user_id
        if account_username is not None:
            record.account_username = normalized_account_username
        record.expires_at = expires_at
        record.revoked_at = None
        record.updated_at = datetime.now(timezone.utc)

    session.commit()
    return record


def refresh_workspace_x_tokens(
    session: Session,
    *,
    workspace_id: str,
    x_client: Optional[XClient] = None,
    redis_client: Optional[Redis] = None,
    now: Optional[datetime] = None,
) -> Optional[str]:
    settings = get_settings()
    if not settings.x_auto_refresh_enabled:
        record_x_token_refresh(workspace_id=workspace_id, status="skipped_disabled")
        return None

    record = _select_active_token_record(session, workspace_id=workspace_id)
    if record is None:
        record_x_token_refresh(workspace_id=workspace_id, status="skipped_missing_token")
        return None

    refresh_token = _safe_decrypt_refresh_token(record)
    if refresh_token is None:
        record_x_token_refresh(workspace_id=workspace_id, status="skipped_no_refresh_token")
        return None

    reference_time = now or datetime.now(timezone.utc)
    lock = _acquire_refresh_lock(
        _safe_redis_client(redis_client),
        workspace_id=workspace_id,
        ttl_seconds=settings.x_refresh_lock_ttl_seconds,
    )
    if not lock.acquired:
        record_x_token_refresh(workspace_id=workspace_id, status="skipped_lock")
        session.expire_all()
        latest_record = _select_active_token_record(session, workspace_id=workspace_id)
        if latest_record is None:
            return None
        latest_token = _safe_decrypt_access_token(latest_record)
        if latest_token is None:
            return None
        latest_expiration = _normalize_expiration(latest_record.expires_at)
        if latest_expiration is not None and latest_expiration <= reference_time:
            return None
        return latest_token

    try:
        client = x_client or get_x_client()
        token_payload = client.refresh_access_token(refresh_token=refresh_token)
        access_token = token_payload.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise XClientError("X token refresh response missing access_token")

        refresh_payload = token_payload.get("refresh_token")
        rotated_refresh_token = (
            str(refresh_payload).strip() if isinstance(refresh_payload, str) and str(refresh_payload).strip() else refresh_token
        )
        scope_payload = token_payload.get("scope")
        resolved_scope = (
            str(scope_payload).strip() if isinstance(scope_payload, str) and str(scope_payload).strip() else record.scope
        )
        token_type = str(token_payload.get("token_type") or record.token_type or "bearer")
        expires_in = _coerce_expires_in(token_payload.get("expires_in"))

        upsert_workspace_x_tokens(
            session,
            workspace_id=workspace_id,
            access_token=access_token,
            refresh_token=rotated_refresh_token,
            token_type=token_type,
            scope=resolved_scope,
            expires_in=expires_in,
            account_user_id=record.account_user_id,
            account_username=record.account_username,
        )
        record_x_token_refresh(workspace_id=workspace_id, status="success")
        return access_token.strip()
    except Exception:
        session.rollback()
        record_x_token_refresh(workspace_id=workspace_id, status="failed")
        return None
    finally:
        _release_refresh_lock(lock)


def get_workspace_x_access_token(
    session: Session,
    *,
    workspace_id: str,
    x_client: Optional[XClient] = None,
    redis_client: Optional[Redis] = None,
) -> Optional[str]:
    record = _select_active_token_record(session, workspace_id=workspace_id)
    if record is None:
        return None

    access_token = _safe_decrypt_access_token(record)
    if access_token is None:
        return None

    expires_at = _normalize_expiration(record.expires_at)
    if expires_at is None:
        return access_token

    now = datetime.now(timezone.utc)
    settings = get_settings()
    refresh_threshold = now + timedelta(seconds=max(0, settings.x_refresh_skew_seconds))
    if expires_at > refresh_threshold:
        return access_token

    if settings.x_auto_refresh_enabled and record.refresh_token_encrypted:
        refreshed = refresh_workspace_x_tokens(
            session,
            workspace_id=workspace_id,
            x_client=x_client,
            redis_client=redis_client,
            now=now,
        )
        if refreshed is not None:
            return refreshed

    if expires_at > now:
        return access_token
    return None


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
            "connected_reason": "not_connected",
            "token_type": None,
            "scope": None,
            "expires_at": None,
            "updated_at": None,
            "has_refresh_token": False,
            "access_token_valid": False,
            "is_expired": False,
            "can_auto_refresh": False,
            "account_user_id": None,
            "account_username": None,
            "has_publish_scope": False,
            "publish_ready": False,
        }

    settings = get_settings()
    now = datetime.now(timezone.utc)
    revoked = record.revoked_at is not None
    expires_at = _normalize_expiration(record.expires_at)
    token_present = _safe_decrypt_access_token(record) is not None
    access_token_valid = token_present and (expires_at is None or expires_at > now)
    is_expired = bool(expires_at is not None and expires_at <= now)
    has_refresh_token = bool(record.refresh_token_encrypted)
    can_auto_refresh = bool((not revoked) and settings.x_auto_refresh_enabled and has_refresh_token)
    has_publish_scope = _has_required_publish_scope(record.scope)
    connected = bool((not revoked) and (access_token_valid or can_auto_refresh))
    publish_ready = bool(connected and has_publish_scope and record.account_user_id)

    connected_reason = "disconnected"
    if revoked:
        connected_reason = "revoked"
    elif access_token_valid:
        connected_reason = "access_token_valid"
    elif can_auto_refresh and is_expired:
        connected_reason = "expired_but_auto_refresh_available"
    elif can_auto_refresh:
        connected_reason = "auto_refresh_available"
    elif is_expired:
        connected_reason = "access_token_expired"
    elif not token_present:
        connected_reason = "access_token_unavailable"

    return {
        "workspace_id": workspace_id,
        "connected": connected,
        "connected_reason": connected_reason,
        "token_type": record.token_type,
        "scope": record.scope,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        "has_refresh_token": has_refresh_token,
        "access_token_valid": access_token_valid,
        "is_expired": is_expired,
        "can_auto_refresh": can_auto_refresh,
        "account_user_id": record.account_user_id,
        "account_username": record.account_username,
        "has_publish_scope": has_publish_scope,
        "publish_ready": publish_ready,
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


def begin_workspace_x_oauth_authorization(
    *,
    workspace_id: str,
    redis_client: Optional[Redis] = None,
) -> Dict[str, Any]:
    settings = get_settings()
    if not settings.x_client_id.strip():
        raise XClientError("X_CLIENT_ID is not configured")
    if not settings.x_redirect_uri.strip():
        raise XClientError("X_REDIRECT_URI is not configured")
    if not settings.x_authorize_url.strip():
        raise XClientError("X_AUTHORIZE_URL is not configured")

    redis = _safe_redis_client(redis_client)
    if redis is None:
        raise XClientError("Redis is unavailable for OAuth state management")

    scope = _normalize_scope(settings.x_oauth_default_scopes)
    if not scope:
        raise XClientError("X_OAUTH_DEFAULT_SCOPES is not configured")

    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(72)
    code_challenge = _pkce_code_challenge(code_verifier)
    ttl_seconds = settings.x_oauth_state_ttl_seconds
    state_payload = json.dumps(
        {
            "workspace_id": workspace_id,
            "code_verifier": code_verifier,
            "scope": scope,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        separators=(",", ":"),
        ensure_ascii=True,
        sort_keys=True,
    )
    key = _oauth_state_key(state)
    try:
        stored = bool(redis.set(key, state_payload, nx=True, ex=max(1, ttl_seconds)))
    except Exception as exc:
        raise XClientError("Failed to persist OAuth state") from exc
    if not stored:
        raise XClientError("Failed to allocate OAuth state")

    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.x_client_id,
            "redirect_uri": settings.x_redirect_uri,
            "scope": scope,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return {
        "workspace_id": workspace_id,
        "authorize_url": f"{settings.x_authorize_url}?{query}",
        "state": state,
        "expires_in": ttl_seconds,
        "scope": scope,
    }


def _consume_oauth_state(
    *,
    state: str,
    redis_client: Optional[Redis] = None,
) -> Dict[str, Any]:
    redis = _safe_redis_client(redis_client)
    if redis is None:
        raise XClientError("Redis is unavailable for OAuth state validation")
    key = _oauth_state_key(state)
    try:
        raw_value = redis.eval(_CONSUME_OAUTH_STATE_SCRIPT, 1, key)
    except Exception as exc:
        raise XClientError("Failed to consume OAuth state") from exc
    if not raw_value:
        raise XClientError("OAuth state is invalid or expired")
    try:
        payload = json.loads(str(raw_value))
    except Exception as exc:
        raise XClientError("OAuth state payload is invalid") from exc
    if not isinstance(payload, dict):
        raise XClientError("OAuth state payload format is invalid")
    return payload


def complete_workspace_x_oauth_callback(
    session: Session,
    *,
    authorization_code: str,
    state: str,
    x_client: Optional[XClient] = None,
    redis_client: Optional[Redis] = None,
) -> Dict[str, Any]:
    state_payload = _consume_oauth_state(state=state, redis_client=redis_client)
    workspace_id = str(state_payload.get("workspace_id") or "").strip()
    code_verifier = str(state_payload.get("code_verifier") or "").strip()
    if not workspace_id:
        raise XClientError("OAuth state payload missing workspace_id")
    if not code_verifier:
        raise XClientError("OAuth state payload missing code_verifier")

    set_workspace_context(session, workspace_id)
    client = x_client or get_x_client()
    token_payload = client.exchange_code_for_tokens(
        authorization_code=authorization_code,
        code_verifier=code_verifier,
    )
    access_token = token_payload.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise XClientError("X token response missing access_token")

    scope = _normalize_scope(token_payload.get("scope") if isinstance(token_payload.get("scope"), str) else None)
    if not _has_required_publish_scope(scope):
        required_scope = get_settings().x_required_publish_scope.strip()
        raise XClientError(f"X OAuth scope missing required permission: {required_scope}")

    user_payload = client.get_authenticated_user(access_token=access_token.strip())
    account_user_id = str(user_payload.get("id") or "").strip()
    account_username = str(user_payload.get("username") or "").strip()
    if not account_user_id or not account_username:
        raise XClientError("Unable to validate authenticated X account identity")

    refresh_payload = token_payload.get("refresh_token")
    refresh_token = str(refresh_payload).strip() if isinstance(refresh_payload, str) and str(refresh_payload).strip() else None
    token_type = str(token_payload.get("token_type") or "bearer")
    expires_in = _coerce_expires_in(token_payload.get("expires_in"))

    record = upsert_workspace_x_tokens(
        session,
        workspace_id=workspace_id,
        access_token=access_token.strip(),
        refresh_token=refresh_token,
        token_type=token_type,
        scope=scope,
        expires_in=expires_in,
        account_user_id=account_user_id,
        account_username=account_username,
    )
    return {
        "workspace_id": workspace_id,
        "connected": True,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "token_type": record.token_type,
        "scope": record.scope,
        "account_user_id": record.account_user_id,
        "account_username": record.account_username,
        "has_publish_scope": _has_required_publish_scope(record.scope),
    }
