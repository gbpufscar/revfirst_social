"""phase9 telegram seed and daily post drafts

Revision ID: 20260218_0006
Revises: 20260217_0005
Create Date: 2026-02-18

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260218_0006"
down_revision = "20260217_0005"
branch_labels = None
depends_on = None


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "telegram_seeds",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("source_chat_id", sa.String(length=64), nullable=False),
        sa.Column("source_message_id", sa.String(length=64), nullable=False),
        sa.Column("source_user_id", sa.String(length=64), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("style_fingerprint_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "source_chat_id",
            "source_message_id",
            name="uq_telegram_seeds_workspace_chat_message",
        ),
    )
    op.create_index(
        "ix_telegram_seeds_workspace_created_at",
        "telegram_seeds",
        ["workspace_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_telegram_seeds_workspace_user_created_at",
        "telegram_seeds",
        ["workspace_id", "source_user_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "daily_post_drafts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("topic", sa.String(length=120), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("style_memory_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("seed_reference_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("brand_score", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("brand_violations_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("cringe_risk_score", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("cringe_flags_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("publish_action", sa.String(length=20), nullable=True),
        sa.Column("external_post_id", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_daily_post_drafts_workspace_created_at",
        "daily_post_drafts",
        ["workspace_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_daily_post_drafts_workspace_status_created_at",
        "daily_post_drafts",
        ["workspace_id", "status", "created_at"],
        unique=False,
    )

    if _is_postgresql():
        for table_name in ["telegram_seeds", "daily_post_drafts"]:
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
        for table_name in ["daily_post_drafts", "telegram_seeds"]:
            op.execute(f"DROP POLICY IF EXISTS {table_name}_select_policy ON {table_name};")
            op.execute(f"DROP POLICY IF EXISTS {table_name}_insert_policy ON {table_name};")
            op.execute(f"DROP POLICY IF EXISTS {table_name}_update_policy ON {table_name};")
            op.execute(f"DROP POLICY IF EXISTS {table_name}_delete_policy ON {table_name};")

    op.drop_index("ix_daily_post_drafts_workspace_status_created_at", table_name="daily_post_drafts")
    op.drop_index("ix_daily_post_drafts_workspace_created_at", table_name="daily_post_drafts")
    op.drop_table("daily_post_drafts")

    op.drop_index("ix_telegram_seeds_workspace_user_created_at", table_name="telegram_seeds")
    op.drop_index("ix_telegram_seeds_workspace_created_at", table_name="telegram_seeds")
    op.drop_table("telegram_seeds")

