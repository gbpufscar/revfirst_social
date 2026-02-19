"""Blog formatter (preview stub)."""

from __future__ import annotations

from src.channels.base import ChannelPayload
from src.domain.content import ContentObject


class BlogFormatter:
    channel = "blog"

    def format(self, content: ContentObject) -> ChannelPayload:
        title = content.title or "RevFirst blog draft"
        body = content.body.strip()
        if content.cta:
            body = f"{body}\n\nNext step: {content.cta}"
        return ChannelPayload(
            workspace_id=content.workspace_id,
            channel=self.channel,
            title=title,
            body=body,
            metadata=dict(content.metadata),
        )
