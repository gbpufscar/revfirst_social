"""Deterministic mock image provider for local/dev usage."""

from __future__ import annotations

import hashlib

from src.media.providers.base import ImageGenerationOutput, ImageProvider


class MockImageProvider(ImageProvider):
    provider_name = "mock"

    def generate_image(
        self,
        *,
        workspace_id: str,
        channel: str,
        prompt: str,
        width: int,
        height: int,
    ) -> ImageGenerationOutput:
        seed_source = f"{workspace_id}:{channel}:{prompt}:{width}x{height}".encode("utf-8")
        seed = hashlib.sha1(seed_source).hexdigest()[:16]
        image_url = f"https://picsum.photos/seed/{seed}/{width}/{height}"
        return ImageGenerationOutput(
            provider=self.provider_name,
            image_url=image_url,
            width=width,
            height=height,
            mime_type="image/jpeg",
            payload={"seed": seed},
        )
