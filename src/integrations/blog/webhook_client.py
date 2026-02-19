"""Webhook client for blog publishing."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, Optional

import httpx

from src.core.config import get_settings


class BlogWebhookError(RuntimeError):
    """Raised when blog webhook publishing fails."""


class BlogWebhookClient:
    def __init__(
        self,
        *,
        webhook_url: str,
        webhook_token: str = "",
        timeout_seconds: int = 20,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self._webhook_url = webhook_url.strip()
        self._webhook_token = webhook_token.strip()
        self._timeout_seconds = max(1, timeout_seconds)
        self._client = client

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._webhook_token:
            headers["Authorization"] = f"Bearer {self._webhook_token}"
        return headers

    def publish(
        self,
        *,
        title: str,
        markdown: str,
        workspace_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self._webhook_url:
            raise BlogWebhookError("blog_webhook_url_missing")
        if not title.strip():
            raise BlogWebhookError("blog_title_missing")
        if not markdown.strip():
            raise BlogWebhookError("blog_body_missing")

        payload: Dict[str, Any] = {
            "title": title.strip(),
            "markdown": markdown.strip(),
            "workspace_id": workspace_id,
            "metadata": metadata or {},
        }

        if self._client is not None:
            response = self._client.post(
                self._webhook_url,
                headers=self._headers(),
                json=payload,
            )
        else:
            with httpx.Client(timeout=self._timeout_seconds) as client:
                response = client.post(
                    self._webhook_url,
                    headers=self._headers(),
                    json=payload,
                )

        if response.status_code < 200 or response.status_code >= 300:
            detail = response.text.strip()
            if len(detail) > 200:
                detail = detail[:200] + "..."
            raise BlogWebhookError(
                f"blog_webhook_failed status={response.status_code} detail={detail}"
            )

        try:
            body = response.json()
        except Exception as exc:  # pragma: no cover
            raise BlogWebhookError("blog_webhook_invalid_json_response") from exc

        if not isinstance(body, dict):
            raise BlogWebhookError("blog_webhook_invalid_payload")
        return body


@lru_cache(maxsize=1)
def get_blog_webhook_client() -> BlogWebhookClient:
    settings = get_settings()
    return BlogWebhookClient(
        webhook_url=settings.blog_webhook_url,
        webhook_token=settings.blog_webhook_token,
        timeout_seconds=settings.blog_webhook_timeout_seconds,
    )
