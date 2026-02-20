"""x oauth account identity metadata

Revision ID: 20260220_0009
Revises: 20260219_0008
Create Date: 2026-02-20

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260220_0009"
down_revision = "20260219_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("x_oauth_tokens", sa.Column("account_user_id", sa.String(length=64), nullable=True))
    op.add_column("x_oauth_tokens", sa.Column("account_username", sa.String(length=64), nullable=True))
    op.create_index(
        "ix_x_oauth_tokens_workspace_account_user_id",
        "x_oauth_tokens",
        ["workspace_id", "account_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_x_oauth_tokens_workspace_account_user_id", table_name="x_oauth_tokens")
    op.drop_column("x_oauth_tokens", "account_username")
    op.drop_column("x_oauth_tokens", "account_user_id")
