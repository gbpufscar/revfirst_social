"""phase3 billing core

Revision ID: 20260217_0003
Revises: 20260217_0002
Create Date: 2026-02-17

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260217_0003"
down_revision = "20260217_0002"
branch_labels = None
depends_on = None


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("stripe_subscription_id", sa.String(length=128), nullable=False),
        sa.Column("stripe_customer_id", sa.String(length=128), nullable=False),
        sa.Column("plan", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", name="uq_subscriptions_workspace"),
        sa.UniqueConstraint("stripe_subscription_id", name="uq_subscriptions_subscription_id"),
    )
    op.create_index(
        "ix_subscriptions_workspace_created_at",
        "subscriptions",
        ["workspace_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "stripe_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="received"),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("error_message", sa.String(length=255), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_stripe_events_event_id"),
    )
    op.create_index("ix_stripe_events_created_at", "stripe_events", ["created_at"], unique=False)
    op.create_index(
        "ix_stripe_events_workspace_created_at",
        "stripe_events",
        ["workspace_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "usage_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_usage_logs_workspace_created_at",
        "usage_logs",
        ["workspace_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_usage_logs_workspace_action_occurred_at",
        "usage_logs",
        ["workspace_id", "action", "occurred_at"],
        unique=False,
    )

    op.create_table(
        "workspace_daily_usage",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "usage_date", "action", name="uq_workspace_daily_usage_unique"),
    )
    op.create_index(
        "ix_workspace_daily_usage_workspace_created_at",
        "workspace_daily_usage",
        ["workspace_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_workspace_daily_usage_lookup",
        "workspace_daily_usage",
        ["workspace_id", "usage_date", "action"],
        unique=False,
    )

    if _is_postgresql():
        for table_name in ["subscriptions", "usage_logs", "workspace_daily_usage"]:
            op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY;")
            op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY;")
            op.execute(
                f"""
                CREATE POLICY {table_name}_select_policy ON {table_name}
                FOR SELECT USING (workspace_id = app_current_workspace_id());
                """
            )
            op.execute(
                f"""
                CREATE POLICY {table_name}_insert_policy ON {table_name}
                FOR INSERT WITH CHECK (
                    app_current_workspace_id() IS NULL OR workspace_id = app_current_workspace_id()
                );
                """
            )
            op.execute(
                f"""
                CREATE POLICY {table_name}_update_policy ON {table_name}
                FOR UPDATE USING (workspace_id = app_current_workspace_id())
                WITH CHECK (workspace_id = app_current_workspace_id());
                """
            )
            op.execute(
                f"""
                CREATE POLICY {table_name}_delete_policy ON {table_name}
                FOR DELETE USING (workspace_id = app_current_workspace_id());
                """
            )


def downgrade() -> None:
    if _is_postgresql():
        for table_name in ["workspace_daily_usage", "usage_logs", "subscriptions"]:
            op.execute(f"DROP POLICY IF EXISTS {table_name}_select_policy ON {table_name};")
            op.execute(f"DROP POLICY IF EXISTS {table_name}_insert_policy ON {table_name};")
            op.execute(f"DROP POLICY IF EXISTS {table_name}_update_policy ON {table_name};")
            op.execute(f"DROP POLICY IF EXISTS {table_name}_delete_policy ON {table_name};")

    op.drop_index("ix_workspace_daily_usage_lookup", table_name="workspace_daily_usage")
    op.drop_index("ix_workspace_daily_usage_workspace_created_at", table_name="workspace_daily_usage")
    op.drop_table("workspace_daily_usage")

    op.drop_index("ix_usage_logs_workspace_action_occurred_at", table_name="usage_logs")
    op.drop_index("ix_usage_logs_workspace_created_at", table_name="usage_logs")
    op.drop_table("usage_logs")

    op.drop_index("ix_stripe_events_workspace_created_at", table_name="stripe_events")
    op.drop_index("ix_stripe_events_created_at", table_name="stripe_events")
    op.drop_table("stripe_events")

    op.drop_index("ix_subscriptions_workspace_created_at", table_name="subscriptions")
    op.drop_table("subscriptions")

