"""Instagram publisher backed by Meta Graph API integration."""

from __future__ import annotations

from typing import Any, Optional

from src.channels.base import ChannelPayload, ChannelPublishResult
from src.core.config import get_settings
from src.integrations.instagram import (
    InstagramGraphClient,
    InstagramGraphError,
    get_instagram_graph_client,
)


class InstagramPublisher:
    channel = "instagram"

    def __init__(
        self,
        *,
        graph_client: Optional[InstagramGraphClient] = None,
        default_image_url: Optional[str] = None,
    ) -> None:
        settings = get_settings()
        self._graph_client = graph_client
        self._default_image_url = (default_image_url or settings.instagram_default_image_url).strip()

    def _resolve_client(self) -> InstagramGraphClient:
        if self._graph_client is not None:
            return self._graph_client
        return get_instagram_graph_client()

    def _resolve_image_url(self, payload: ChannelPayload) -> str:
        metadata = payload.metadata or {}
        for key in ("image_url", "media_url", "asset_url"):
            value: Any = metadata.get(key)
            if value is None:
                continue
            resolved = str(value).strip()
            if resolved:
                return resolved
        return self._default_image_url

    def publish(self, payload: ChannelPayload) -> ChannelPublishResult:
        caption = payload.body.strip()
        if not caption:
            return ChannelPublishResult(
                channel=self.channel,
                published=False,
                status="failed",
                message="instagram_caption_missing",
                payload={},
            )

        image_url = self._resolve_image_url(payload)
        if not image_url:
            return ChannelPublishResult(
                channel=self.channel,
                published=False,
                status="failed",
                message="instagram_image_url_missing",
                payload={"caption": caption},
            )

        try:
            response = self._resolve_client().publish_caption(
                caption=caption,
                image_url=image_url,
            )
            publish_response = response.get("publish_response")
            external_id = None
            if isinstance(publish_response, dict) and publish_response.get("id"):
                external_id = str(publish_response["id"])
            return ChannelPublishResult(
                channel=self.channel,
                published=True,
                status="published",
                message="Instagram published",
                external_id=external_id,
                payload=response,
            )
        except InstagramGraphError as exc:
            return ChannelPublishResult(
                channel=self.channel,
                published=False,
                status="failed",
                message=str(exc),
                payload={"caption": caption, "image_url": image_url},
            )
