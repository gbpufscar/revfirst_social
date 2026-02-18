import pytest

from src.core.config import get_settings


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
