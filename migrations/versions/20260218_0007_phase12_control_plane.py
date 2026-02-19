"""phase12 control plane via telegram

Revision ID: 20260218_0007
Revises: 20260218_0006
Create Date: 2026-02-18

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260218_0007"
down_revision = "20260218_0006"
branch_labels = None
depends_on = None


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "workspace_control_settings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("is_paused", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "channels_json",
            sa.Text(),
            nullable=False,
            server_default='{"blog":false,"email":false,"instagram":false,"x":true}',
        ),
        sa.Column("reply_limit_override", sa.Integer(), nullable=True),
        sa.Column("post_limit_override", sa.Integer(), nullable=True),
        sa.Column("limit_override_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", name="uq_workspace_control_settings_workspace"),
    )
    op.create_index(
        "ix_workspace_control_settings_workspace_created_at",
        "workspace_control_settings",
        ["workspace_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "admin_actions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=True),
        sa.Column("telegram_user_id", sa.String(length=32), nullable=False),
        sa.Column("command", sa.String(length=80), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("error_message", sa.String(length=255), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_admin_actions_workspace_created_at",
        "admin_actions",
        ["workspace_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_admin_actions_workspace_command_created_at",
        "admin_actions",
        ["workspace_id", "command", "created_at"],
        unique=False,
    )

    op.create_table(
        "approval_queue_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("item_type", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.String(length=40), nullable=True),
        sa.Column("source_ref_id", sa.String(length=64), nullable=True),
        sa.Column("intent", sa.String(length=32), nullable=True),
        sa.Column("opportunity_score", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("approved_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_post_id", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["rejected_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_approval_queue_workspace_idempotency"),
    )
    op.create_index(
        "ix_approval_queue_items_workspace_created_at",
        "approval_queue_items",
        ["workspace_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_approval_queue_items_workspace_status_created_at",
        "approval_queue_items",
        ["workspace_id", "status", "created_at"],
        unique=False,
    )

    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("pipeline_name", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="started"),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=80), nullable=True),
        sa.Column("actor_user_id", sa.String(length=36), nullable=True),
        sa.Column("telegram_user_id", sa.String(length=32), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("error_message", sa.String(length=255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "pipeline_name",
            "idempotency_key",
            name="uq_pipeline_runs_workspace_pipeline_idempotency",
        ),
    )
    op.create_index(
        "ix_pipeline_runs_workspace_created_at",
        "pipeline_runs",
        ["workspace_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_pipeline_runs_workspace_pipeline_created_at",
        "pipeline_runs",
        ["workspace_id", "pipeline_name", "created_at"],
        unique=False,
    )

    if _is_postgresql():
        for table_name in [
            "workspace_control_settings",
            "admin_actions",
            "approval_queue_items",
            "pipeline_runs",
        ]:
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
        for table_name in [
            "pipeline_runs",
            "approval_queue_items",
            "admin_actions",
            "workspace_control_settings",
        ]:
            op.execute(f"DROP POLICY IF EXISTS {table_name}_select_policy ON {table_name};")
            op.execute(f"DROP POLICY IF EXISTS {table_name}_insert_policy ON {table_name};")
            op.execute(f"DROP POLICY IF EXISTS {table_name}_update_policy ON {table_name};")
            op.execute(f"DROP POLICY IF EXISTS {table_name}_delete_policy ON {table_name};")

    op.drop_index("ix_pipeline_runs_workspace_pipeline_created_at", table_name="pipeline_runs")
    op.drop_index("ix_pipeline_runs_workspace_created_at", table_name="pipeline_runs")
    op.drop_table("pipeline_runs")

    op.drop_index("ix_approval_queue_items_workspace_status_created_at", table_name="approval_queue_items")
    op.drop_index("ix_approval_queue_items_workspace_created_at", table_name="approval_queue_items")
    op.drop_table("approval_queue_items")

    op.drop_index("ix_admin_actions_workspace_command_created_at", table_name="admin_actions")
    op.drop_index("ix_admin_actions_workspace_created_at", table_name="admin_actions")
    op.drop_table("admin_actions")

    op.drop_index("ix_workspace_control_settings_workspace_created_at", table_name="workspace_control_settings")
    op.drop_table("workspace_control_settings")
