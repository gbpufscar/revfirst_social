from __future__ import annotations

from pathlib import Path


def test_phase12_migration_declares_control_plane_tables_and_rls() -> None:
    migration_path = Path("migrations/versions/20260218_0007_phase12_control_plane.py")
    source = migration_path.read_text(encoding="utf-8")

    assert "workspace_control_settings" in source
    assert "admin_actions" in source
    assert "approval_queue_items" in source
    assert "pipeline_runs" in source

    assert "uq_workspace_control_settings_workspace" in source
    assert "uq_approval_queue_workspace_idempotency" in source
    assert "uq_pipeline_runs_workspace_pipeline_idempotency" in source

    assert "ix_admin_actions_workspace_created_at" in source
    assert "ix_approval_queue_items_workspace_status_created_at" in source
    assert "ix_pipeline_runs_workspace_pipeline_created_at" in source

    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "CREATE POLICY" in source
