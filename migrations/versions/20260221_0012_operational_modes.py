"""operational modes for workspace control settings

Revision ID: 20260221_0012
Revises: 20260221_0011
Create Date: 2026-02-21

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260221_0012"
down_revision = "20260221_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace_control_settings",
        sa.Column(
            "operational_mode",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'semi_autonomous'"),
        ),
    )
    op.add_column(
        "workspace_control_settings",
        sa.Column(
            "last_mode_change_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.add_column(
        "workspace_control_settings",
        sa.Column("mode_changed_by_user_id", sa.String(length=36), nullable=True),
    )
    if op.get_bind().dialect.name != "sqlite":
        op.create_foreign_key(
            "fk_workspace_control_settings_mode_changed_by_user_id",
            "workspace_control_settings",
            "users",
            ["mode_changed_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint(
            "fk_workspace_control_settings_mode_changed_by_user_id",
            "workspace_control_settings",
            type_="foreignkey",
        )
    op.drop_column("workspace_control_settings", "mode_changed_by_user_id")
    op.drop_column("workspace_control_settings", "last_mode_change_at")
    op.drop_column("workspace_control_settings", "operational_mode")
