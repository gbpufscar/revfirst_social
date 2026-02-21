"""x strategy discovery candidates

Revision ID: 20260221_0011
Revises: 20260221_0010
Create Date: 2026-02-21

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260221_0011"
down_revision = "20260221_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "x_strategy_discovery_candidates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("account_user_id", sa.String(length=64), nullable=False),
        sa.Column("account_username", sa.String(length=64), nullable=True),
        sa.Column("source_query", sa.Text(), nullable=True),
        sa.Column("signal_post_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("followers_count", sa.Integer(), nullable=True),
        sa.Column("tweet_count", sa.Integer(), nullable=True),
        sa.Column("avg_engagement", sa.Float(), nullable=True),
        sa.Column("cadence_per_day", sa.Float(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("rationale_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("status", sa.String(length=24), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("reviewed_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discovered_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "account_user_id", name="uq_x_strategy_discovery_workspace_account"),
    )
    op.create_index(
        "ix_x_strategy_discovery_workspace_status_score",
        "x_strategy_discovery_candidates",
        ["workspace_id", "status", "score"],
        unique=False,
    )
    op.create_index(
        "ix_x_strategy_discovery_workspace_discovered_at",
        "x_strategy_discovery_candidates",
        ["workspace_id", "discovered_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_x_strategy_discovery_workspace_discovered_at", table_name="x_strategy_discovery_candidates")
    op.drop_index("ix_x_strategy_discovery_workspace_status_score", table_name="x_strategy_discovery_candidates")
    op.drop_table("x_strategy_discovery_candidates")

