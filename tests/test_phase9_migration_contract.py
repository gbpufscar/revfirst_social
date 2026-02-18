from pathlib import Path


def test_phase9_migration_contains_telegram_seed_and_daily_post_tables() -> None:
    migration_path = Path("migrations/versions/20260218_0006_telegram_seed_daily_post.py")
    content = migration_path.read_text(encoding="utf-8")

    assert "telegram_seeds" in content
    assert "daily_post_drafts" in content
    assert "uq_telegram_seeds_workspace_chat_message" in content
    assert "ix_telegram_seeds_workspace_created_at" in content
    assert "ix_daily_post_drafts_workspace_status_created_at" in content
    assert "ENABLE ROW LEVEL SECURITY" in content
    assert "CREATE POLICY" in content

