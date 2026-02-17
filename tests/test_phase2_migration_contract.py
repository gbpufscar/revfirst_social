from __future__ import annotations

from pathlib import Path


def test_phase2_migration_declares_core_tables_and_rls_contract() -> None:
    migration_path = Path("migrations/versions/20260217_0002_multitenant_core.py")
    source = migration_path.read_text(encoding="utf-8")

    assert "\"users\"," in source
    assert "\"workspaces\"," in source
    assert "\"workspace_users\"," in source
    assert "\"roles\"," in source
    assert "\"api_keys\"," in source

    assert "ix_workspace_users_workspace_created_at" in source
    assert "ix_api_keys_workspace_created_at" in source
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "app_current_workspace_id" in source
