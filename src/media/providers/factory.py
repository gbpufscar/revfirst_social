"""Factory to resolve active image provider."""

from __future__ import annotations

from functools import lru_cache

from src.core.config import get_settings
from src.media.providers.base import ImageProvider
from src.media.providers.gemini_provider import GeminiImageProvider
from src.media.providers.mock_provider import MockImageProvider
from src.media.providers.webhook_provider import WebhookImageProvider


@lru_cache(maxsize=1)
def get_image_provider() -> ImageProvider:
    settings = get_settings()
    provider = settings.image_provider.strip().lower()
    if provider == "gemini":
        return GeminiImageProvider(
            api_key=settings.gemini_image_api_key,
            model=settings.gemini_image_model,
            base_url=settings.gemini_image_api_base_url,
            timeout_seconds=settings.gemini_image_timeout_seconds,
        )
    if provider == "webhook":
        return WebhookImageProvider(
            webhook_url=settings.image_webhook_url,
            webhook_token=settings.image_webhook_token,
            timeout_seconds=settings.image_webhook_timeout_seconds,
        )
    return MockImageProvider()


def reset_image_provider_cache() -> None:
    get_image_provider.cache_clear()
