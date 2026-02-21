import pytest

from src.core.config import get_settings


def _set_minimum_production_env(monkeypatch) -> None:
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "prod-secret-key")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://app:password@db:5432/revfirst_social")
    monkeypatch.setenv("REDIS_URL", "redis://cache:6379/0")
    monkeypatch.setenv("X_CLIENT_ID", "x-client-id-prod")
    monkeypatch.setenv("X_CLIENT_SECRET", "x-client-secret-prod")
    monkeypatch.setenv("X_REDIRECT_URI", "https://social.revfirst.cloud/integrations/x/oauth/callback")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "telegram-webhook-secret")
    monkeypatch.setenv("TELEGRAM_ADMINS_FILE_PATH", "/run/secrets/telegram_admins.yaml")
    monkeypatch.setenv("APP_PUBLIC_BASE_URL", "https://social.revfirst.cloud")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_ENABLED", "false")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_INTERNAL_KEY", "internal-key-prod")


def test_requires_secret_key_in_production(monkeypatch) -> None:
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "")
    get_settings.cache_clear()

    with pytest.raises(ValueError):
        get_settings()

    get_settings.cache_clear()


def test_loads_environment_values(monkeypatch) -> None:
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./data/test_phase1.sqlite")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/9")
    get_settings.cache_clear()

    settings = get_settings()
    assert settings.env == "development"
    assert settings.secret_key == "test-secret"
    assert settings.database_url.endswith("test_phase1.sqlite")
    assert settings.redis_url.endswith("/9")

    get_settings.cache_clear()


def test_rejects_invalid_observability_limits(monkeypatch) -> None:
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "1.2")
    monkeypatch.setenv("IP_RATE_LIMIT_REQUESTS_PER_WINDOW", "0")
    get_settings.cache_clear()

    with pytest.raises(ValueError):
        get_settings()

    get_settings.cache_clear()


def test_rejects_invalid_scheduler_intervals(monkeypatch) -> None:
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("SCHEDULER_GROWTH_COLLECTION_INTERVAL_HOURS", "0")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="SCHEDULER_GROWTH_COLLECTION_INTERVAL_HOURS"):
        get_settings()

    monkeypatch.setenv("SCHEDULER_GROWTH_COLLECTION_INTERVAL_HOURS", "24")
    monkeypatch.setenv("SCHEDULER_STRATEGY_SCAN_INTERVAL_HOURS", "0")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="SCHEDULER_STRATEGY_SCAN_INTERVAL_HOURS"):
        get_settings()

    monkeypatch.setenv("SCHEDULER_STRATEGY_SCAN_INTERVAL_HOURS", "24")
    monkeypatch.setenv("SCHEDULER_STRATEGY_DISCOVERY_INTERVAL_HOURS", "0")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="SCHEDULER_STRATEGY_DISCOVERY_INTERVAL_HOURS"):
        get_settings()

    monkeypatch.setenv("SCHEDULER_STRATEGY_DISCOVERY_INTERVAL_HOURS", "24")
    monkeypatch.setenv("X_STRATEGY_DISCOVERY_MAX_RESULTS", "0")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="X_STRATEGY_DISCOVERY_MAX_RESULTS"):
        get_settings()

    monkeypatch.setenv("X_STRATEGY_DISCOVERY_MAX_RESULTS", "30")
    monkeypatch.setenv("X_STRATEGY_DISCOVERY_MAX_CANDIDATES", "0")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="X_STRATEGY_DISCOVERY_MAX_CANDIDATES"):
        get_settings()

    monkeypatch.setenv("X_STRATEGY_DISCOVERY_MAX_CANDIDATES", "10")
    monkeypatch.setenv("X_STRATEGY_CANDIDATE_MIN_FOLLOWERS", "-1")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="X_STRATEGY_CANDIDATE_MIN_FOLLOWERS"):
        get_settings()

    monkeypatch.setenv("X_STRATEGY_CANDIDATE_MIN_FOLLOWERS", "100")
    monkeypatch.setenv("X_STRATEGY_CANDIDATE_MAX_FOLLOWERS", "50")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="X_STRATEGY_CANDIDATE_MAX_FOLLOWERS"):
        get_settings()

    get_settings.cache_clear()


def test_requires_internal_publish_key_when_direct_api_enabled(monkeypatch) -> None:
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_ENABLED", "true")
    monkeypatch.setenv("PUBLISHING_DIRECT_API_INTERNAL_KEY", "")
    get_settings.cache_clear()

    with pytest.raises(ValueError):
        get_settings()

    get_settings.cache_clear()


def test_requires_all_mandatory_production_secrets(monkeypatch) -> None:
    _set_minimum_production_env(monkeypatch)
    monkeypatch.setenv("X_CLIENT_SECRET", "")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="X_CLIENT_SECRET"):
        get_settings()

    get_settings.cache_clear()


def test_rejects_direct_publish_api_enabled_in_production(monkeypatch) -> None:
    _set_minimum_production_env(monkeypatch)
    monkeypatch.setenv("PUBLISHING_DIRECT_API_ENABLED", "true")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="PUBLISHING_DIRECT_API_ENABLED must be false in production"):
        get_settings()

    get_settings.cache_clear()
