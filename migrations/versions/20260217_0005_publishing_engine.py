"""phase7 publishing engine

Revision ID: 20260217_0005
Revises: 20260217_0004
Create Date: 2026-02-17

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260217_0005"
down_revision = "20260217_0004"
branch_labels = None
depends_on = None


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "publish_audit_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False, server_default="x"),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("request_text", sa.Text(), nullable=False),
        sa.Column("in_reply_to_tweet_id", sa.String(length=64), nullable=True),
        sa.Column("target_thread_id", sa.String(length=64), nullable=True),
        sa.Column("target_author_id", sa.String(length=64), nullable=True),
        sa.Column("external_post_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_message", sa.String(length=255), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_publish_audit_logs_workspace_created_at",
        "publish_audit_logs",
        ["workspace_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_publish_audit_logs_workspace_status_created_at",
        "publish_audit_logs",
        ["workspace_id", "status", "created_at"],
        unique=False,
    )

    op.create_table(
        "publish_cooldowns",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("scope_key", sa.String(length=128), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_action", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "scope", "scope_key", name="uq_publish_cooldowns_workspace_scope_key"),
    )
    op.create_index(
        "ix_publish_cooldowns_workspace_created_at",
        "publish_cooldowns",
        ["workspace_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_publish_cooldowns_lookup",
        "publish_cooldowns",
        ["workspace_id", "scope", "scope_key"],
        unique=False,
    )

    if _is_postgresql():
        for table_name in ["publish_audit_logs", "publish_cooldowns"]:
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
        for table_name in ["publish_cooldowns", "publish_audit_logs"]:
            op.execute(f"DROP POLICY IF EXISTS {table_name}_select_policy ON {table_name};")
            op.execute(f"DROP POLICY IF EXISTS {table_name}_insert_policy ON {table_name};")
            op.execute(f"DROP POLICY IF EXISTS {table_name}_update_policy ON {table_name};")
            op.execute(f"DROP POLICY IF EXISTS {table_name}_delete_policy ON {table_name};")

    op.drop_index("ix_publish_cooldowns_lookup", table_name="publish_cooldowns")
    op.drop_index("ix_publish_cooldowns_workspace_created_at", table_name="publish_cooldowns")
    op.drop_table("publish_cooldowns")

    op.drop_index("ix_publish_audit_logs_workspace_status_created_at", table_name="publish_audit_logs")
    op.drop_index("ix_publish_audit_logs_workspace_created_at", table_name="publish_audit_logs")
    op.drop_table("publish_audit_logs")

