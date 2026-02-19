"""Instagram formatter (caption preview)."""

from __future__ import annotations

from src.channels.base import ChannelPayload
from src.domain.content import ContentObject


class InstagramFormatter:
    channel = "instagram"

    def format(self, content: ContentObject) -> ChannelPayload:
        caption = content.body.strip()
        hashtags = content.metadata.get("hashtags")
        if isinstance(hashtags, list) and hashtags:
            normalized = [f"#{str(tag).strip().lstrip('#')}" for tag in hashtags if str(tag).strip()]
            if normalized:
                caption = f"{caption}\n\n{' '.join(normalized)}"
        return ChannelPayload(
            workspace_id=content.workspace_id,
            channel=self.channel,
            title=content.title,
            body=caption,
            metadata=dict(content.metadata),
        )
