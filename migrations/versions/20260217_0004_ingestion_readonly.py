"""phase5 ingestion layer readonly

Revision ID: 20260217_0004
Revises: 20260217_0003
Create Date: 2026-02-17

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260217_0004"
down_revision = "20260217_0003"
branch_labels = None
depends_on = None


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "x_oauth_tokens",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False, server_default="x"),
        sa.Column("access_token_hash", sa.String(length=64), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=64), nullable=True),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("token_type", sa.String(length=32), nullable=False, server_default="bearer"),
        sa.Column("scope", sa.String(length=255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "provider", name="uq_x_oauth_tokens_workspace_provider"),
    )
    op.create_index(
        "ix_x_oauth_tokens_workspace_created_at",
        "x_oauth_tokens",
        ["workspace_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "ingestion_candidates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="x"),
        sa.Column("source_tweet_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=True),
        sa.Column("author_id", sa.String(length=64), nullable=True),
        sa.Column("author_handle", sa.String(length=64), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=12), nullable=True),
        sa.Column("url", sa.String(length=255), nullable=True),
        sa.Column("intent", sa.String(length=32), nullable=False),
        sa.Column("opportunity_score", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ingested"),
        sa.Column("raw_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "source",
            "source_tweet_id",
            name="uq_ingestion_candidates_workspace_source_tweet",
        ),
    )
    op.create_index(
        "ix_ingestion_candidates_workspace_created_at",
        "ingestion_candidates",
        ["workspace_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_ingestion_candidates_workspace_intent_score",
        "ingestion_candidates",
        ["workspace_id", "intent", "opportunity_score"],
        unique=False,
    )

    if _is_postgresql():
        for table_name in ["x_oauth_tokens", "ingestion_candidates"]:
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
        for table_name in ["ingestion_candidates", "x_oauth_tokens"]:
            op.execute(f"DROP POLICY IF EXISTS {table_name}_select_policy ON {table_name};")
            op.execute(f"DROP POLICY IF EXISTS {table_name}_insert_policy ON {table_name};")
            op.execute(f"DROP POLICY IF EXISTS {table_name}_update_policy ON {table_name};")
            op.execute(f"DROP POLICY IF EXISTS {table_name}_delete_policy ON {table_name};")

    op.drop_index("ix_ingestion_candidates_workspace_intent_score", table_name="ingestion_candidates")
    op.drop_index("ix_ingestion_candidates_workspace_created_at", table_name="ingestion_candidates")
    op.drop_table("ingestion_candidates")

    op.drop_index("ix_x_oauth_tokens_workspace_created_at", table_name="x_oauth_tokens")
    op.drop_table("x_oauth_tokens")

