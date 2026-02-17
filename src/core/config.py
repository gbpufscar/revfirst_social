"""Central runtime configuration for RevFirst_Social."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    database_url: str = "postgresql+psycopg2://app:password@postgres:5432/revfirst_social"
    redis_url: str = "redis://redis:6379/0"
    secret_key: str = ""
    env: str = "development"
    log_level: str = "INFO"
    port: int = 8000
    app_name: str = "revfirst_social"
    app_version: str = "0.1.0"
    jwt_algorithm: str = "HS256"
    access_token_exp_minutes: int = 60
    stripe_api_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_signature_tolerance_seconds: int = 300
    plans_file_path: str = "config/plans.yaml"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


def _validate(settings: Settings) -> Settings:
    if settings.env.lower() in {"prod", "production"} and not settings.secret_key:
        raise ValueError("SECRET_KEY is required when ENV=production.")
    return settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return validated settings as a cached singleton."""

    return _validate(Settings())
