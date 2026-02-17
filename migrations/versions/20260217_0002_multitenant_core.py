"""phase2 multitenant core

Revision ID: 20260217_0002
Revises: 20260217_0001
Create Date: 2026-02-17

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260217_0002"
down_revision = "20260217_0001"
branch_labels = None
depends_on = None


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.UniqueConstraint("name", name="uq_roles_name"),
    )

    role_table = sa.table("roles", sa.column("name", sa.String()))
    op.bulk_insert(role_table, [{"name": "owner"}, {"name": "admin"}, {"name": "member"}])

    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("plan", sa.String(length=32), nullable=False, server_default="free"),
        sa.Column("stripe_customer_id", sa.String(length=128), nullable=True),
        sa.Column("subscription_status", sa.String(length=32), nullable=False, server_default="inactive"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_workspaces_name"),
    )

    op.create_table(
        "workspace_users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_workspace_users_workspace_user"),
    )
    op.create_index(
        "ix_workspace_users_workspace_created_at",
        "workspace_users",
        ["workspace_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("key_prefix", sa.String(length=20), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
        sa.UniqueConstraint("workspace_id", "key_prefix", name="uq_api_keys_workspace_prefix"),
    )
    op.create_index(
        "ix_api_keys_workspace_created_at",
        "api_keys",
        ["workspace_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "workspace_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.String(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workspace_events_workspace_created_at",
        "workspace_events",
        ["workspace_id", "created_at"],
        unique=False,
    )

    if _is_postgresql():
        op.execute(
            """
            CREATE OR REPLACE FUNCTION app_current_workspace_id()
            RETURNS text
            LANGUAGE sql
            STABLE
            AS $$
                SELECT NULLIF(current_setting('app.current_workspace_id', true), '');
            $$;
            """
        )

        for table_name in ["workspaces", "workspace_users", "api_keys", "workspace_events"]:
            op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY;")
            op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY;")

        op.execute(
            """
            CREATE POLICY workspaces_select_policy ON workspaces
            FOR SELECT USING (id = app_current_workspace_id());
            """
        )
        op.execute(
            """
            CREATE POLICY workspaces_insert_policy ON workspaces
            FOR INSERT WITH CHECK (
                app_current_workspace_id() IS NULL OR id = app_current_workspace_id()
            );
            """
        )
        op.execute(
            """
            CREATE POLICY workspaces_update_policy ON workspaces
            FOR UPDATE USING (id = app_current_workspace_id())
            WITH CHECK (id = app_current_workspace_id());
            """
        )
        op.execute(
            """
            CREATE POLICY workspaces_delete_policy ON workspaces
            FOR DELETE USING (id = app_current_workspace_id());
            """
        )

        for table_name in ["workspace_users", "api_keys", "workspace_events"]:
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
        for table_name in ["workspace_events", "api_keys", "workspace_users", "workspaces"]:
            op.execute(f"DROP POLICY IF EXISTS {table_name}_select_policy ON {table_name};")
            op.execute(f"DROP POLICY IF EXISTS {table_name}_insert_policy ON {table_name};")
            op.execute(f"DROP POLICY IF EXISTS {table_name}_update_policy ON {table_name};")
            op.execute(f"DROP POLICY IF EXISTS {table_name}_delete_policy ON {table_name};")

        op.execute("DROP POLICY IF EXISTS workspaces_select_policy ON workspaces;")
        op.execute("DROP POLICY IF EXISTS workspaces_insert_policy ON workspaces;")
        op.execute("DROP POLICY IF EXISTS workspaces_update_policy ON workspaces;")
        op.execute("DROP POLICY IF EXISTS workspaces_delete_policy ON workspaces;")
        op.execute("DROP FUNCTION IF EXISTS app_current_workspace_id;")

    op.drop_index("ix_workspace_events_workspace_created_at", table_name="workspace_events")
    op.drop_table("workspace_events")

    op.drop_index("ix_api_keys_workspace_created_at", table_name="api_keys")
    op.drop_table("api_keys")

    op.drop_index("ix_workspace_users_workspace_created_at", table_name="workspace_users")
    op.drop_table("workspace_users")

    op.drop_table("workspaces")
    op.drop_table("roles")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
