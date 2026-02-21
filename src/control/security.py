"""Telegram control-plane authorization and command permissions."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Set

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.config import get_settings
from src.storage.models import Role, WorkspaceUser


class ControlAuthorizationError(RuntimeError):
    pass


@dataclass(frozen=True)
class TelegramAdminBinding:
    telegram_user_id: str
    user_id: str
    allowed_roles: Set[str]


@dataclass(frozen=True)
class TelegramAdminDirectory:
    allowed_telegram_ids: Set[str]
    bindings: Dict[str, TelegramAdminBinding]


@dataclass(frozen=True)
class TelegramNotificationChannelStatus:
    status: str
    has_bot_token: bool
    allowed_ids_count: int
    reasons: Set[str]

    @property
    def degraded(self) -> bool:
        return self.status == "degraded"


@dataclass(frozen=True)
class ControlActor:
    telegram_user_id: str
    user_id: str
    role: str


COMMAND_ROLE_MATRIX: Dict[str, Set[str]] = {
    "help": {"owner", "admin", "member"},
    "status": {"owner", "admin", "member"},
    "mode": {"owner", "admin"},
    "stability": {"owner", "admin", "member"},
    "metrics": {"owner", "admin", "member"},
    "growth": {"owner", "admin", "member"},
    "growth_weekly": {"owner", "admin", "member"},
    "daily_report": {"owner", "admin", "member"},
    "weekly_report": {"owner", "admin", "member"},
    "strategy_report": {"owner", "admin", "member"},
    "logs": {"owner", "admin", "member"},
    "queue": {"owner", "admin", "member"},
    "preview": {"owner", "admin", "member"},
    "approve": {"owner", "admin"},
    "reject": {"owner", "admin"},
    "pause": {"owner", "admin"},
    "resume": {"owner", "admin"},
    "run": {"owner", "admin"},
    "channel": {"owner", "admin"},
    "limit": {"owner", "admin"},
    "seed": {"owner", "admin", "member"},
    "ack_kill_switch": {"owner"},
    "strategy_scan": {"owner", "admin"},
    "strategy_discover": {"owner", "admin"},
}


def _resolve_admins_path() -> Path:
    settings = get_settings()
    configured = Path(settings.telegram_admins_file_path)
    if configured.is_absolute():
        return configured
    return Path.cwd() / configured


def _normalize_role_list(raw_roles: object) -> Set[str]:
    if not isinstance(raw_roles, list):
        return set()
    normalized: Set[str] = set()
    for role in raw_roles:
        if isinstance(role, str) and role.strip():
            normalized.add(role.strip().lower())
    return normalized


@lru_cache(maxsize=1)
def load_admin_directory() -> TelegramAdminDirectory:
    path = _resolve_admins_path()
    if not path.exists():
        return TelegramAdminDirectory(allowed_telegram_ids=set(), bindings={})

    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        return TelegramAdminDirectory(allowed_telegram_ids=set(), bindings={})

    allowed_ids: Set[str] = set()
    for value in payload.get("allowed_telegram_ids", []):
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized:
            allowed_ids.add(normalized)

    bindings: Dict[str, TelegramAdminBinding] = {}
    admins_raw = payload.get("admins", [])
    if isinstance(admins_raw, list):
        for item in admins_raw:
            if not isinstance(item, dict):
                continue
            telegram_user_id = str(item.get("telegram_user_id", "")).strip()
            user_id = str(item.get("user_id", "")).strip()
            if not telegram_user_id or not user_id:
                continue
            allowed_roles = _normalize_role_list(item.get("allowed_roles"))
            bindings[telegram_user_id] = TelegramAdminBinding(
                telegram_user_id=telegram_user_id,
                user_id=user_id,
                allowed_roles=allowed_roles,
            )

    return TelegramAdminDirectory(
        allowed_telegram_ids=allowed_ids,
        bindings=bindings,
    )


def reset_admin_directory_cache() -> None:
    load_admin_directory.cache_clear()


def get_telegram_notification_channel_status() -> TelegramNotificationChannelStatus:
    settings = get_settings()
    directory = load_admin_directory()

    has_bot_token = bool(settings.telegram_bot_token.strip())
    allowed_ids_count = len(directory.allowed_telegram_ids)
    reasons: Set[str] = set()

    if not has_bot_token:
        reasons.add("telegram_bot_token_missing")
    if allowed_ids_count == 0:
        reasons.add("allowed_telegram_ids_empty")

    status = "degraded" if reasons else "healthy"
    return TelegramNotificationChannelStatus(
        status=status,
        has_bot_token=has_bot_token,
        allowed_ids_count=allowed_ids_count,
        reasons=reasons,
    )


def _workspace_role(session: Session, *, workspace_id: str, user_id: str) -> Optional[str]:
    statement = (
        select(Role.name)
        .join(WorkspaceUser, WorkspaceUser.role_id == Role.id)
        .where(
            WorkspaceUser.workspace_id == workspace_id,
            WorkspaceUser.user_id == user_id,
        )
        .limit(1)
    )
    role = session.scalar(statement)
    return str(role) if role else None


def resolve_control_actor(
    session: Session,
    *,
    workspace_id: str,
    telegram_user_id: str,
) -> ControlActor:
    directory = load_admin_directory()
    if telegram_user_id not in directory.allowed_telegram_ids:
        raise ControlAuthorizationError("unauthorized_telegram_user")

    binding = directory.bindings.get(telegram_user_id)
    if binding is None:
        raise ControlAuthorizationError("missing_telegram_user_binding")

    role = _workspace_role(
        session,
        workspace_id=workspace_id,
        user_id=binding.user_id,
    )
    if role is None:
        raise ControlAuthorizationError("workspace_membership_not_found")

    normalized_role = role.lower()
    if binding.allowed_roles and normalized_role not in binding.allowed_roles:
        raise ControlAuthorizationError("role_not_allowed_for_telegram_binding")

    return ControlActor(
        telegram_user_id=telegram_user_id,
        user_id=binding.user_id,
        role=normalized_role,
    )


def assert_command_permission(actor: ControlActor, *, command_name: str) -> None:
    allowed_roles = COMMAND_ROLE_MATRIX.get(command_name)
    if not allowed_roles:
        raise ControlAuthorizationError("unknown_command")
    if actor.role not in allowed_roles:
        raise ControlAuthorizationError("insufficient_role")
