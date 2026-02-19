from __future__ import annotations

from pathlib import Path


def test_phase16_media_migration_declares_tables_indexes_and_rls() -> None:
    migration_path = Path("migrations/versions/20260219_0008_phase16_media_infra.py")
    source = migration_path.read_text(encoding="utf-8")

    assert "media_assets" in source
    assert "media_jobs" in source

    assert "uq_media_jobs_workspace_idempotency" in source
    assert "ix_media_assets_workspace_channel_created_at" in source
    assert "ix_media_jobs_workspace_status_created_at" in source

    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "CREATE POLICY" in source
