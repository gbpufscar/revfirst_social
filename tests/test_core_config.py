import pytest

from src.core.config import get_settings


def test_requires_secret_key_in_production(monkeypatch) -> None:
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("SECRET_KEY", raising=False)
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
