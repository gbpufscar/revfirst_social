"""Instagram publisher stub (no Graph API publish in Phase 13)."""

from __future__ import annotations

from src.channels.base import ChannelPayload, ChannelPublishResult


class InstagramPublisher:
    channel = "instagram"

    def publish(self, payload: ChannelPayload) -> ChannelPublishResult:
        return ChannelPublishResult(
            channel=self.channel,
            published=False,
            status="preview_only",
            message="Instagram publish is disabled in Phase 13",
            payload={
                "caption": payload.body,
            },
        )
