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
    runtime_file_path: str = "config/runtime.yaml"
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
    email_api_key: str = ""
    email_api_base_url: str = "https://api.resend.com"
    email_api_timeout_seconds: int = 20
    email_from_address: str = ""
    email_default_recipients: str = ""
    blog_webhook_url: str = ""
    blog_webhook_token: str = ""
    blog_webhook_timeout_seconds: int = 20
    instagram_graph_access_token: str = ""
    instagram_graph_account_id: str = ""
    instagram_graph_api_base_url: str = "https://graph.facebook.com/v20.0"
    instagram_graph_api_timeout_seconds: int = 20
    instagram_default_image_url: str = ""
    instagram_default_schedule_hours_ahead: int = 0
    app_public_base_url: str = ""
    media_storage_path: str = "data/media"
    image_generation_enabled: bool = True
    image_provider: str = "mock"
    image_webhook_url: str = ""
    image_webhook_token: str = ""
    image_webhook_timeout_seconds: int = 20
    gemini_image_api_key: str = ""
    gemini_image_model: str = "gemini-2.0-flash-preview-image-generation"
    gemini_image_api_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_image_timeout_seconds: int = 30
    x_default_open_calls_query: str = (
        "\"drop your saas\" OR \"share your startup\" OR \"what are you building\" "
        "OR \"show your product\" lang:en -is:retweet"
    )
    publish_thread_cooldown_minutes: int = 45
    publish_author_cooldown_minutes: int = 30
    publish_max_text_chars: int = 280
    scheduler_workspace_lock_ttl_seconds: int = 300
    scheduler_max_workspaces_per_run: int = 50
    scheduler_candidate_evaluation_limit: int = 5
    telegram_webhook_secret: str = ""
    telegram_seed_max_text_chars: int = 1200
    daily_post_seed_limit: int = 10
    daily_post_default_topic: str = "builder growth"
    daily_post_auto_publish_default: bool = False
    telegram_admins_file_path: str = "config/telegram_admins.yaml"
    control_run_lock_ttl_seconds: int = 120
    control_limit_override_ttl_seconds: int = 86400
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.0
    metrics_enabled: bool = True
    ip_rate_limit_enabled: bool = True
    ip_rate_limit_requests_per_window: int = 120
    ip_rate_limit_window_seconds: int = 60

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


def _validate(settings: Settings) -> Settings:
    if settings.env.lower() in {"prod", "production"} and not settings.secret_key:
        raise ValueError("SECRET_KEY is required when ENV=production.")
    if settings.sentry_traces_sample_rate < 0 or settings.sentry_traces_sample_rate > 1:
        raise ValueError("SENTRY_TRACES_SAMPLE_RATE must be between 0 and 1.")
    if settings.ip_rate_limit_requests_per_window <= 0:
        raise ValueError("IP_RATE_LIMIT_REQUESTS_PER_WINDOW must be positive.")
    if settings.ip_rate_limit_window_seconds <= 0:
        raise ValueError("IP_RATE_LIMIT_WINDOW_SECONDS must be positive.")
    if settings.instagram_default_schedule_hours_ahead < 0:
        raise ValueError("INSTAGRAM_DEFAULT_SCHEDULE_HOURS_AHEAD must be zero or positive.")
    if settings.image_provider.strip().lower() not in {"mock", "webhook", "gemini"}:
        raise ValueError("IMAGE_PROVIDER must be one of: mock, webhook, gemini.")
    return settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return validated settings as a cached singleton."""

    return _validate(Settings())
