"""Gemini image generation provider."""

from __future__ import annotations

import base64
from typing import Any, Dict, Optional

import httpx

from src.media.providers.base import ImageGenerationOutput, ImageProvider, ImageProviderError


class GeminiImageProvider(ImageProvider):
    provider_name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        timeout_seconds: int = 30,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self._api_key = api_key.strip()
        self._model = model.strip()
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = max(1, timeout_seconds)
        self._client = client

    def _endpoint(self) -> str:
        if not self._api_key:
            raise ImageProviderError("gemini_image_api_key_missing")
        if not self._model:
            raise ImageProviderError("gemini_image_model_missing")
        return f"{self._base_url}/models/{self._model}:generateContent?key={self._api_key}"

    @staticmethod
    def _decode_inline_data(data: Dict[str, Any]) -> bytes:
        encoded = str(data.get("data") or "").strip()
        if not encoded:
            raise ImageProviderError("gemini_image_missing_inline_data")
        return base64.b64decode(encoded)

    def generate_image(
        self,
        *,
        workspace_id: str,
        channel: str,
        prompt: str,
        width: int,
        height: int,
    ) -> ImageGenerationOutput:
        del workspace_id
        sized_prompt = (
            f"{prompt}\n\nGenerate image for {channel} in {width}x{height} resolution. "
            "No text overlay. Brand-safe, builder-first style."
        )
        request_body = {
            "contents": [{"parts": [{"text": sized_prompt}]}],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
            },
        }

        if self._client is not None:
            response = self._client.post(self._endpoint(), json=request_body)
        else:
            with httpx.Client(timeout=self._timeout_seconds) as client:
                response = client.post(self._endpoint(), json=request_body)

        if response.status_code < 200 or response.status_code >= 300:
            detail = response.text.strip()
            if len(detail) > 240:
                detail = detail[:240] + "..."
            raise ImageProviderError(f"gemini_image_request_failed status={response.status_code} detail={detail}")

        try:
            body: Dict[str, Any] = response.json()
        except Exception as exc:  # pragma: no cover
            raise ImageProviderError("gemini_image_invalid_json_response") from exc

        candidates = body.get("candidates")
        if not isinstance(candidates, list):
            raise ImageProviderError("gemini_image_missing_candidates")

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            content = candidate.get("content")
            if not isinstance(content, dict):
                continue
            parts = content.get("parts")
            if not isinstance(parts, list):
                continue
            for part in parts:
                if not isinstance(part, dict):
                    continue
                inline_data = part.get("inlineData") or part.get("inline_data")
                if isinstance(inline_data, dict) and inline_data.get("data"):
                    mime_type = str(inline_data.get("mimeType") or "image/png")
                    image_bytes = self._decode_inline_data(inline_data)
                    return ImageGenerationOutput(
                        provider=self.provider_name,
                        mime_type=mime_type,
                        width=width,
                        height=height,
                        image_bytes=image_bytes,
                        payload=body,
                    )

                text_value = part.get("text")
                if isinstance(text_value, str):
                    stripped = text_value.strip()
                    if stripped.startswith("http://") or stripped.startswith("https://"):
                        return ImageGenerationOutput(
                            provider=self.provider_name,
                            mime_type="image/png",
                            width=width,
                            height=height,
                            image_url=stripped,
                            payload=body,
                        )

        raise ImageProviderError("gemini_image_output_not_found")
