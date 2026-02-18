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
    token_encryption_key: str = ""
    stripe_api_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_signature_tolerance_seconds: int = 300
    plans_file_path: str = "config/plans.yaml"
    x_client_id: str = ""
    x_client_secret: str = ""
    x_redirect_uri: str = ""
    x_token_url: str = "https://api.twitter.com/2/oauth2/token"
    x_search_url: str = "https://api.twitter.com/2/tweets/search/recent"
    x_publish_url: str = "https://api.twitter.com/2/tweets"
    x_api_timeout_seconds: int = 20
    x_default_open_calls_query: str = (
        "\"drop your saas\" OR \"share your startup\" OR \"what are you building\" "
        "OR \"show your product\" lang:en -is:retweet"
    )
    publish_thread_cooldown_minutes: int = 45
    publish_author_cooldown_minutes: int = 30
    publish_max_text_chars: int = 280

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
