"""editorial queue scheduling fields and canonical statuses

Revision ID: 20260221_0013
Revises: 20260221_0012
Create Date: 2026-02-21

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260221_0013"
down_revision = "20260221_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "approval_queue_items",
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "approval_queue_items",
        sa.Column("publish_window_key", sa.String(length=24), nullable=True),
    )
    op.add_column(
        "approval_queue_items",
        sa.Column(
            "editorial_priority",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    op.create_index(
        "ix_approval_queue_items_workspace_status_scheduled_for",
        "approval_queue_items",
        ["workspace_id", "status", "scheduled_for"],
        unique=False,
    )

    op.execute(
        sa.text(
            "UPDATE approval_queue_items "
            "SET status = 'pending_review' "
            "WHERE status = 'pending'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE approval_queue_items "
            "SET status = 'approved_scheduled' "
            "WHERE status = 'approved'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE approval_queue_items "
            "SET status = 'pending' "
            "WHERE status = 'pending_review'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE approval_queue_items "
            "SET status = 'approved' "
            "WHERE status = 'approved_scheduled'"
        )
    )

    op.drop_index(
        "ix_approval_queue_items_workspace_status_scheduled_for",
        table_name="approval_queue_items",
    )
    op.drop_column("approval_queue_items", "editorial_priority")
    op.drop_column("approval_queue_items", "publish_window_key")
    op.drop_column("approval_queue_items", "scheduled_for")

