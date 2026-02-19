"""Email publisher stub (no real send in Phase 13)."""

from __future__ import annotations

from src.channels.base import ChannelPayload, ChannelPublishResult


class EmailPublisher:
    channel = "email"

    def publish(self, payload: ChannelPayload) -> ChannelPublishResult:
        return ChannelPublishResult(
            channel=self.channel,
            published=False,
            status="preview_only",
            message="Email publish is disabled in Phase 13",
            payload={
                "subject": payload.title,
                "body": payload.body,
            },
        )
