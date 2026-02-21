"""x growth and strategy agents

Revision ID: 20260221_0010
Revises: 20260220_0009
Create Date: 2026-02-21

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260221_0010"
down_revision = "20260220_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "x_account_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("account_user_id", sa.String(length=64), nullable=True),
        sa.Column("account_username", sa.String(length=64), nullable=True),
        sa.Column("followers_count", sa.Integer(), nullable=True),
        sa.Column("following_count", sa.Integer(), nullable=True),
        sa.Column("tweet_count", sa.Integer(), nullable=True),
        sa.Column("listed_count", sa.Integer(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_x_account_snapshots_workspace_captured_at",
        "x_account_snapshots",
        ["workspace_id", "captured_at"],
        unique=False,
    )
    op.create_index(
        "ix_x_account_snapshots_workspace_account_captured_at",
        "x_account_snapshots",
        ["workspace_id", "account_user_id", "captured_at"],
        unique=False,
    )

    op.create_table(
        "x_post_metrics_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("external_post_id", sa.String(length=64), nullable=False),
        sa.Column("like_count", sa.Integer(), nullable=True),
        sa.Column("reply_count", sa.Integer(), nullable=True),
        sa.Column("repost_count", sa.Integer(), nullable=True),
        sa.Column("quote_count", sa.Integer(), nullable=True),
        sa.Column("bookmark_count", sa.Integer(), nullable=True),
        sa.Column("impression_count", sa.Integer(), nullable=True),
        sa.Column("has_image", sa.Boolean(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_x_post_metrics_snapshots_workspace_captured_at",
        "x_post_metrics_snapshots",
        ["workspace_id", "captured_at"],
        unique=False,
    )
    op.create_index(
        "ix_x_post_metrics_snapshots_workspace_post_captured_at",
        "x_post_metrics_snapshots",
        ["workspace_id", "external_post_id", "captured_at"],
        unique=False,
    )

    op.create_table(
        "x_growth_insights",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("period_type", sa.String(length=16), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("kpis_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("recommendations_json", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_x_growth_insights_workspace_created_at",
        "x_growth_insights",
        ["workspace_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_x_growth_insights_workspace_period_created_at",
        "x_growth_insights",
        ["workspace_id", "period_type", "created_at"],
        unique=False,
    )

    op.create_table(
        "x_strategy_watchlist",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("account_user_id", sa.String(length=64), nullable=False),
        sa.Column("account_username", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default=sa.text("'active'")),
        sa.Column("added_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["added_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "account_user_id", name="uq_x_strategy_watchlist_workspace_account"),
    )
    op.create_index(
        "ix_x_strategy_watchlist_workspace_status_added_at",
        "x_strategy_watchlist",
        ["workspace_id", "status", "added_at"],
        unique=False,
    )

    op.create_table(
        "x_competitor_posts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("watched_account_user_id", sa.String(length=64), nullable=False),
        sa.Column("watched_account_username", sa.String(length=64), nullable=True),
        sa.Column("external_post_id", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("post_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("like_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("reply_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("repost_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("quote_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("impression_count", sa.Integer(), nullable=True),
        sa.Column("has_image", sa.Boolean(), nullable=True),
        sa.Column("raw_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "watched_account_user_id",
            "external_post_id",
            name="uq_x_competitor_posts_workspace_account_post",
        ),
    )
    op.create_index(
        "ix_x_competitor_posts_workspace_account_captured_at",
        "x_competitor_posts",
        ["workspace_id", "watched_account_user_id", "captured_at"],
        unique=False,
    )

    op.create_table(
        "x_strategy_patterns",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("period_window", sa.String(length=32), nullable=False),
        sa.Column("pattern_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("confidence_score", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_x_strategy_patterns_workspace_generated_at",
        "x_strategy_patterns",
        ["workspace_id", "generated_at"],
        unique=False,
    )

    op.create_table(
        "x_strategy_recommendations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("period_window", sa.String(length=32), nullable=False),
        sa.Column("recommendation_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("rationale_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_x_strategy_recommendations_workspace_created_at",
        "x_strategy_recommendations",
        ["workspace_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_x_strategy_recommendations_workspace_created_at", table_name="x_strategy_recommendations")
    op.drop_table("x_strategy_recommendations")

    op.drop_index("ix_x_strategy_patterns_workspace_generated_at", table_name="x_strategy_patterns")
    op.drop_table("x_strategy_patterns")

    op.drop_index("ix_x_competitor_posts_workspace_account_captured_at", table_name="x_competitor_posts")
    op.drop_table("x_competitor_posts")

    op.drop_index("ix_x_strategy_watchlist_workspace_status_added_at", table_name="x_strategy_watchlist")
    op.drop_table("x_strategy_watchlist")

    op.drop_index("ix_x_growth_insights_workspace_period_created_at", table_name="x_growth_insights")
    op.drop_index("ix_x_growth_insights_workspace_created_at", table_name="x_growth_insights")
    op.drop_table("x_growth_insights")

    op.drop_index("ix_x_post_metrics_snapshots_workspace_post_captured_at", table_name="x_post_metrics_snapshots")
    op.drop_index("ix_x_post_metrics_snapshots_workspace_captured_at", table_name="x_post_metrics_snapshots")
    op.drop_table("x_post_metrics_snapshots")

    op.drop_index("ix_x_account_snapshots_workspace_account_captured_at", table_name="x_account_snapshots")
    op.drop_index("ix_x_account_snapshots_workspace_captured_at", table_name="x_account_snapshots")
    op.drop_table("x_account_snapshots")
