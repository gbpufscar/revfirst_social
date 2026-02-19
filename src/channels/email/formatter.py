"""Email payload formatter (preview stub)."""

from __future__ import annotations

from src.channels.base import ChannelPayload
from src.domain.content import ContentObject


class EmailFormatter:
    channel = "email"

    def format(self, content: ContentObject) -> ChannelPayload:
        subject = content.title or "RevFirst update"
        body = content.body.strip()
        if content.cta:
            body = f"{body}\n\nCTA: {content.cta}"
        return ChannelPayload(
            workspace_id=content.workspace_id,
            channel=self.channel,
            title=subject,
            body=body,
            metadata=dict(content.metadata),
        )
