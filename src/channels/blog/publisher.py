"""Blog publisher stub (no real CMS publish in Phase 13)."""

from __future__ import annotations

from src.channels.base import ChannelPayload, ChannelPublishResult


class BlogPublisher:
    channel = "blog"

    def publish(self, payload: ChannelPayload) -> ChannelPublishResult:
        return ChannelPublishResult(
            channel=self.channel,
            published=False,
            status="preview_only",
            message="Blog publish is disabled in Phase 13",
            payload={
                "title": payload.title,
                "markdown": payload.body,
            },
        )
