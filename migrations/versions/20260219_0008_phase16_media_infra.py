"""phase16 media infrastructure and image jobs

Revision ID: 20260219_0008
Revises: 20260218_0007
Create Date: 2026-02-19

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260219_0008"
down_revision = "20260218_0007"
branch_labels = None
depends_on = None


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "media_assets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("source_type", sa.String(length=24), nullable=False, server_default="generated"),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="mock"),
        sa.Column("purpose", sa.String(length=64), nullable=True),
        sa.Column("channel", sa.String(length=24), nullable=False),
        sa.Column("mime_type", sa.String(length=64), nullable=False, server_default="image/png"),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("storage_backend", sa.String(length=24), nullable=False, server_default="external_url"),
        sa.Column("storage_path", sa.String(length=255), nullable=True),
        sa.Column("public_url", sa.String(length=500), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("prompt_text", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_media_assets_workspace_created_at",
        "media_assets",
        ["workspace_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_media_assets_workspace_channel_created_at",
        "media_assets",
        ["workspace_id", "channel", "created_at"],
        unique=False,
    )

    op.create_table(
        "media_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="mock"),
        sa.Column("channel", sa.String(length=24), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.String(length=40), nullable=True),
        sa.Column("source_ref_id", sa.String(length=64), nullable=True),
        sa.Column("requested_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("result_asset_id", sa.String(length=36), nullable=True),
        sa.Column("error_message", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=80), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["result_asset_id"], ["media_assets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_media_jobs_workspace_idempotency"),
    )
    op.create_index(
        "ix_media_jobs_workspace_created_at",
        "media_jobs",
        ["workspace_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_media_jobs_workspace_status_created_at",
        "media_jobs",
        ["workspace_id", "status", "created_at"],
        unique=False,
    )

    if _is_postgresql():
        for table_name in ["media_assets", "media_jobs"]:
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
        for table_name in ["media_jobs", "media_assets"]:
            op.execute(f"DROP POLICY IF EXISTS {table_name}_select_policy ON {table_name};")
            op.execute(f"DROP POLICY IF EXISTS {table_name}_insert_policy ON {table_name};")
            op.execute(f"DROP POLICY IF EXISTS {table_name}_update_policy ON {table_name};")
            op.execute(f"DROP POLICY IF EXISTS {table_name}_delete_policy ON {table_name};")

    op.drop_index("ix_media_jobs_workspace_status_created_at", table_name="media_jobs")
    op.drop_index("ix_media_jobs_workspace_created_at", table_name="media_jobs")
    op.drop_table("media_jobs")

    op.drop_index("ix_media_assets_workspace_channel_created_at", table_name="media_assets")
    op.drop_index("ix_media_assets_workspace_created_at", table_name="media_assets")
    op.drop_table("media_assets")
