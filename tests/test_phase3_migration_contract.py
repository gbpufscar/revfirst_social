from __future__ import annotations

from pathlib import Path


def test_phase3_migration_declares_billing_tables_and_idempotency_contract() -> None:
    migration_path = Path("migrations/versions/20260217_0003_billing_core.py")
    source = migration_path.read_text(encoding="utf-8")

    assert "\"subscriptions\"," in source
    assert "\"stripe_events\"," in source
    assert "\"usage_logs\"," in source
    assert "\"workspace_daily_usage\"," in source

    assert "uq_stripe_events_event_id" in source
    assert "ix_subscriptions_workspace_created_at" in source
    assert "ix_usage_logs_workspace_created_at" in source
    assert "ix_workspace_daily_usage_lookup" in source
    assert "ENABLE ROW LEVEL SECURITY" in source

