from __future__ import annotations

from pathlib import Path


def test_phase7_migration_declares_publish_tables_and_rls() -> None:
    migration_path = Path("migrations/versions/20260217_0005_publishing_engine.py")
    source = migration_path.read_text(encoding="utf-8")

    assert "\"publish_audit_logs\"," in source
    assert "\"publish_cooldowns\"," in source
    assert "uq_publish_cooldowns_workspace_scope_key" in source
    assert "ix_publish_audit_logs_workspace_created_at" in source
    assert "ix_publish_cooldowns_workspace_created_at" in source
    assert "ENABLE ROW LEVEL SECURITY" in source

