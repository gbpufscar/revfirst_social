"""Provider contracts for image generation backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol


class ImageProviderError(RuntimeError):
    """Raised when an image provider cannot fulfill a generation request."""


@dataclass(frozen=True)
class ImageGenerationOutput:
    provider: str
    mime_type: str = "image/png"
    width: Optional[int] = None
    height: Optional[int] = None
    image_url: Optional[str] = None
    image_bytes: Optional[bytes] = None
    payload: Dict[str, Any] = field(default_factory=dict)


class ImageProvider(Protocol):
    provider_name: str

    def generate_image(
        self,
        *,
        workspace_id: str,
        channel: str,
        prompt: str,
        width: int,
        height: int,
    ) -> ImageGenerationOutput:
        raise NotImplementedError
