"""Meta Graph API client for Instagram publishing."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, Optional

import httpx

from src.core.config import get_settings


class InstagramGraphError(RuntimeError):
    """Raised when Instagram Graph API operations fail."""


class InstagramGraphClient:
    def __init__(
        self,
        *,
        access_token: str,
        account_id: str,
        base_url: str = "https://graph.facebook.com/v20.0",
        timeout_seconds: int = 20,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self._access_token = access_token.strip()
        self._account_id = account_id.strip()
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = max(1, timeout_seconds)
        self._client = client

    def _assert_ready(self) -> None:
        if not self._access_token:
            raise InstagramGraphError("instagram_access_token_missing")
        if not self._account_id:
            raise InstagramGraphError("instagram_account_id_missing")

    def _request(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self._base_url}/{path.lstrip('/')}"
        if self._client is not None:
            response = self._client.post(url, data=payload)
        else:
            with httpx.Client(timeout=self._timeout_seconds) as client:
                response = client.post(url, data=payload)

        if response.status_code < 200 or response.status_code >= 300:
            detail = response.text.strip()
            if len(detail) > 240:
                detail = detail[:240] + "..."
            raise InstagramGraphError(
                f"instagram_graph_request_failed status={response.status_code} detail={detail}"
            )

        try:
            body = response.json()
        except Exception as exc:  # pragma: no cover
            raise InstagramGraphError("instagram_graph_invalid_json_response") from exc

        if not isinstance(body, dict):
            raise InstagramGraphError("instagram_graph_invalid_payload")
        return body

    def create_media(
        self,
        *,
        caption: str,
        image_url: str,
    ) -> Dict[str, Any]:
        self._assert_ready()

        if not caption.strip():
            raise InstagramGraphError("instagram_caption_missing")
        if not image_url.strip():
            raise InstagramGraphError("instagram_image_url_missing")

        return self._request(
            f"{self._account_id}/media",
            {
                "access_token": self._access_token,
                "caption": caption.strip(),
                "image_url": image_url.strip(),
            },
        )

    def publish_media(self, *, creation_id: str) -> Dict[str, Any]:
        self._assert_ready()
        if not creation_id.strip():
            raise InstagramGraphError("instagram_creation_id_missing")

        return self._request(
            f"{self._account_id}/media_publish",
            {
                "access_token": self._access_token,
                "creation_id": creation_id.strip(),
            },
        )

    def publish_caption(
        self,
        *,
        caption: str,
        image_url: str,
    ) -> Dict[str, Any]:
        creation_response = self.create_media(caption=caption, image_url=image_url)
        creation_id = str(creation_response.get("id") or "").strip()
        if not creation_id:
            raise InstagramGraphError("instagram_graph_missing_creation_id")
        publish_response = self.publish_media(creation_id=creation_id)
        return {
            "creation_response": creation_response,
            "publish_response": publish_response,
        }


@lru_cache(maxsize=1)
def get_instagram_graph_client() -> InstagramGraphClient:
    settings = get_settings()
    return InstagramGraphClient(
        access_token=settings.instagram_graph_access_token,
        account_id=settings.instagram_graph_account_id,
        base_url=settings.instagram_graph_api_base_url,
        timeout_seconds=settings.instagram_graph_api_timeout_seconds,
    )
