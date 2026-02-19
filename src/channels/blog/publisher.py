"""Blog publisher backed by webhook integration."""

from __future__ import annotations

from typing import Optional

from src.channels.base import ChannelPayload, ChannelPublishResult
from src.integrations.blog import BlogWebhookClient, BlogWebhookError, get_blog_webhook_client


class BlogPublisher:
    channel = "blog"

    def __init__(self, *, webhook_client: Optional[BlogWebhookClient] = None) -> None:
        self._webhook_client = webhook_client

    def _resolve_client(self) -> BlogWebhookClient:
        if self._webhook_client is not None:
            return self._webhook_client
        return get_blog_webhook_client()

    def publish(self, payload: ChannelPayload) -> ChannelPublishResult:
        title = (payload.title or "RevFirst blog draft").strip()
        if not title:
            title = "RevFirst blog draft"

        try:
            response = self._resolve_client().publish(
                title=title,
                markdown=payload.body,
                workspace_id=payload.workspace_id,
                metadata=payload.metadata,
            )
            post_id = response.get("id")
            external_id = str(post_id) if post_id else None
            return ChannelPublishResult(
                channel=self.channel,
                published=True,
                status="published",
                message="Blog published",
                external_id=external_id,
                payload=response,
            )
        except BlogWebhookError as exc:
            return ChannelPublishResult(
                channel=self.channel,
                published=False,
                status="failed",
                message=str(exc),
                payload={"title": title},
            )
