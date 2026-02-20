"""SQLAlchemy ORM models for multi-tenant core and billing foundation."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional
import uuid

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.storage.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    plan: Mapped[str] = mapped_column(String(32), nullable=False, default="free")
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    subscription_status: Mapped[str] = mapped_column(String(32), nullable=False, default="inactive")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    members: Mapped[list[WorkspaceUser]] = relationship("WorkspaceUser", back_populates="workspace")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)


class WorkspaceUser(Base):
    __tablename__ = "workspace_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("roles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    workspace: Mapped[Workspace] = relationship("Workspace", back_populates="members")
    role: Mapped[Role] = relationship("Role")

    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_users_workspace_user"),
        Index("ix_workspace_users_workspace_created_at", "workspace_id", "created_at"),
    )


class APIKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by_user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "key_prefix", name="uq_api_keys_workspace_prefix"),
        Index("ix_api_keys_workspace_created_at", "workspace_id", "created_at"),
    )


class WorkspaceEvent(Base):
    """Initial workspace-scoped domain table to enforce tenant-id pattern from day 1."""

    __tablename__ = "workspace_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(String, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (Index("ix_workspace_events_workspace_created_at", "workspace_id", "created_at"),)


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    stripe_subscription_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    stripe_customer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    plan: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_subscriptions_workspace"),
        Index("ix_subscriptions_workspace_created_at", "workspace_id", "created_at"),
    )


class StripeEvent(Base):
    __tablename__ = "stripe_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    workspace_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="received")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_stripe_events_created_at", "created_at"),
        Index("ix_stripe_events_workspace_created_at", "workspace_id", "created_at"),
    )


class UsageLog(Base):
    __tablename__ = "usage_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_usage_logs_workspace_created_at", "workspace_id", "created_at"),
        Index("ix_usage_logs_workspace_action_occurred_at", "workspace_id", "action", "occurred_at"),
    )


class WorkspaceDailyUsage(Base):
    __tablename__ = "workspace_daily_usage"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    usage_date: Mapped[date] = mapped_column(Date, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "usage_date", "action", name="uq_workspace_daily_usage_unique"),
        Index("ix_workspace_daily_usage_workspace_created_at", "workspace_id", "created_at"),
        Index("ix_workspace_daily_usage_lookup", "workspace_id", "usage_date", "action"),
    )


class XOAuthToken(Base):
    __tablename__ = "x_oauth_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="x")
    access_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    refresh_token_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    token_type: Mapped[str] = mapped_column(String(32), nullable=False, default="bearer")
    scope: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    account_user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    account_username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "provider", name="uq_x_oauth_tokens_workspace_provider"),
        Index("ix_x_oauth_tokens_workspace_created_at", "workspace_id", "created_at"),
    )


class IngestionCandidate(Base):
    __tablename__ = "ingestion_candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="x")
    source_tweet_id: Mapped[str] = mapped_column(String(64), nullable=False)
    conversation_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    author_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    author_handle: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    intent: Mapped[str] = mapped_column(String(32), nullable=False)
    opportunity_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ingested")
    raw_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "source",
            "source_tweet_id",
            name="uq_ingestion_candidates_workspace_source_tweet",
        ),
        Index("ix_ingestion_candidates_workspace_created_at", "workspace_id", "created_at"),
        Index(
            "ix_ingestion_candidates_workspace_intent_score",
            "workspace_id",
            "intent",
            "opportunity_score",
        ),
    )


class PublishAuditLog(Base):
    __tablename__ = "publish_audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False, default="x")
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    request_text: Mapped[str] = mapped_column(Text, nullable=False)
    in_reply_to_tweet_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    target_thread_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    target_author_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    external_post_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_publish_audit_logs_workspace_created_at", "workspace_id", "created_at"),
        Index("ix_publish_audit_logs_workspace_status_created_at", "workspace_id", "status", "created_at"),
    )


class PublishCooldown(Base):
    __tablename__ = "publish_cooldowns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(128), nullable=False)
    cooldown_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_action: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "scope", "scope_key", name="uq_publish_cooldowns_workspace_scope_key"),
        Index("ix_publish_cooldowns_workspace_created_at", "workspace_id", "created_at"),
        Index("ix_publish_cooldowns_lookup", "workspace_id", "scope", "scope_key"),
    )


class TelegramSeed(Base):
    __tablename__ = "telegram_seeds"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_chat_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_message_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    style_fingerprint_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "source_chat_id",
            "source_message_id",
            name="uq_telegram_seeds_workspace_chat_message",
        ),
        Index("ix_telegram_seeds_workspace_created_at", "workspace_id", "created_at"),
        Index("ix_telegram_seeds_workspace_user_created_at", "workspace_id", "source_user_id", "created_at"),
    )


class DailyPostDraft(Base):
    __tablename__ = "daily_post_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    topic: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    style_memory_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    seed_reference_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    brand_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    brand_violations_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    cringe_risk_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cringe_flags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    publish_action: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    external_post_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_daily_post_drafts_workspace_created_at", "workspace_id", "created_at"),
        Index("ix_daily_post_drafts_workspace_status_created_at", "workspace_id", "status", "created_at"),
    )


class WorkspaceControlSetting(Base):
    __tablename__ = "workspace_control_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    is_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    channels_json: Mapped[str] = mapped_column(Text, nullable=False, default='{"blog":false,"email":false,"instagram":false,"x":true}')
    reply_limit_override: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    post_limit_override: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    limit_override_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_workspace_control_settings_workspace"),
        Index("ix_workspace_control_settings_workspace_created_at", "workspace_id", "created_at"),
    )


class AdminAction(Base):
    __tablename__ = "admin_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_user_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    telegram_user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    command: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    result_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_admin_actions_workspace_created_at", "workspace_id", "created_at"),
        Index("ix_admin_actions_workspace_command_created_at", "workspace_id", "command", "created_at"),
    )


class ApprovalQueueItem(Base):
    __tablename__ = "approval_queue_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_type: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_kind: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    source_ref_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    intent: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    opportunity_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    approved_by_user_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_by_user_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    rejected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    published_post_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_approval_queue_workspace_idempotency"),
        Index("ix_approval_queue_items_workspace_created_at", "workspace_id", "created_at"),
        Index("ix_approval_queue_items_workspace_status_created_at", "workspace_id", "status", "created_at"),
    )


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(String(24), nullable=False, default="generated")
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="mock")
    purpose: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    channel: Mapped[str] = mapped_column(String(24), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(64), nullable=False, default="image/png")
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    storage_backend: Mapped[str] = mapped_column(String(24), nullable=False, default="external_url")
    storage_path: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    public_url: Mapped[str] = mapped_column(String(500), nullable=False)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    prompt_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_media_assets_workspace_created_at", "workspace_id", "created_at"),
        Index("ix_media_assets_workspace_channel_created_at", "workspace_id", "channel", "created_at"),
    )


class MediaJob(Base):
    __tablename__ = "media_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="mock")
    channel: Mapped[str] = mapped_column(String(24), nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_kind: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    source_ref_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    requested_by_user_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    result_asset_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("media_assets.id", ondelete="SET NULL"),
        nullable=True,
    )
    error_message: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_media_jobs_workspace_idempotency"),
        Index("ix_media_jobs_workspace_created_at", "workspace_id", "created_at"),
        Index("ix_media_jobs_workspace_status_created_at", "workspace_id", "status", "created_at"),
    )


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    pipeline_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="started")
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    actor_user_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    telegram_user_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error_message: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "pipeline_name", "idempotency_key", name="uq_pipeline_runs_workspace_pipeline_idempotency"),
        Index("ix_pipeline_runs_workspace_created_at", "workspace_id", "created_at"),
        Index("ix_pipeline_runs_workspace_pipeline_created_at", "workspace_id", "pipeline_name", "created_at"),
    )
