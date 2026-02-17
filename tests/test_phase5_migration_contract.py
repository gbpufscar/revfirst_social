from __future__ import annotations

from pathlib import Path


def test_phase5_migration_declares_oauth_and_ingestion_tables() -> None:
    migration_path = Path("migrations/versions/20260217_0004_ingestion_readonly.py")
    source = migration_path.read_text(encoding="utf-8")

    assert "\"x_oauth_tokens\"," in source
    assert "\"ingestion_candidates\"," in source
    assert "uq_x_oauth_tokens_workspace_provider" in source
    assert "uq_ingestion_candidates_workspace_source_tweet" in source
    assert "ix_x_oauth_tokens_workspace_created_at" in source
    assert "ix_ingestion_candidates_workspace_created_at" in source
    assert "ix_ingestion_candidates_workspace_intent_score" in source
    assert "ENABLE ROW LEVEL SECURITY" in source

