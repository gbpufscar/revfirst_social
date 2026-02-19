"""Webhook-backed image provider."""

from __future__ import annotations

import base64
from typing import Any, Dict, Optional

import httpx

from src.media.providers.base import ImageGenerationOutput, ImageProvider, ImageProviderError


class WebhookImageProvider(ImageProvider):
    provider_name = "webhook"

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

    @staticmethod
    def _decode_base64(data: str) -> bytes:
        cleaned = data.strip()
        if cleaned.startswith("data:") and "," in cleaned:
            cleaned = cleaned.split(",", 1)[1]
        return base64.b64decode(cleaned)

    def generate_image(
        self,
        *,
        workspace_id: str,
        channel: str,
        prompt: str,
        width: int,
        height: int,
    ) -> ImageGenerationOutput:
        if not self._webhook_url:
            raise ImageProviderError("image_webhook_url_missing")

        payload = {
            "workspace_id": workspace_id,
            "channel": channel,
            "prompt": prompt,
            "width": width,
            "height": height,
        }

        if self._client is not None:
            response = self._client.post(self._webhook_url, headers=self._headers(), json=payload)
        else:
            with httpx.Client(timeout=self._timeout_seconds) as client:
                response = client.post(self._webhook_url, headers=self._headers(), json=payload)

        if response.status_code < 200 or response.status_code >= 300:
            detail = response.text.strip()
            if len(detail) > 240:
                detail = detail[:240] + "..."
            raise ImageProviderError(f"image_webhook_failed status={response.status_code} detail={detail}")

        try:
            body: Dict[str, Any] = response.json()
        except Exception as exc:  # pragma: no cover
            raise ImageProviderError("image_webhook_invalid_json_response") from exc

        image_url = str(body.get("image_url") or body.get("url") or "").strip() or None
        image_base64 = str(body.get("image_base64") or body.get("b64_json") or "").strip() or None
        mime_type = str(body.get("mime_type") or "image/png").strip() or "image/png"
        out_width = body.get("width")
        out_height = body.get("height")

        image_bytes = None
        if image_base64:
            try:
                image_bytes = self._decode_base64(image_base64)
            except Exception as exc:  # pragma: no cover
                raise ImageProviderError("image_webhook_invalid_base64_payload") from exc

        if not image_url and not image_bytes:
            raise ImageProviderError("image_webhook_missing_image")

        return ImageGenerationOutput(
            provider=self.provider_name,
            mime_type=mime_type,
            width=int(out_width) if isinstance(out_width, int) else width,
            height=int(out_height) if isinstance(out_height, int) else height,
            image_url=image_url,
            image_bytes=image_bytes,
            payload=body,
        )
