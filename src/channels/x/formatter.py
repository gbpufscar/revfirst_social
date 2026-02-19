"""X payload formatter."""

from __future__ import annotations

from src.channels.base import ChannelPayload
from src.domain.content import ContentObject


class XFormatter:
    channel = "x"

    def __init__(self, *, max_chars: int = 280) -> None:
        self._max_chars = max(20, max_chars)

    def format(self, content: ContentObject) -> ChannelPayload:
        text = content.body.strip()
        if len(text) > self._max_chars:
            text = text[: self._max_chars - 1].rstrip() + "."

        metadata = dict(content.metadata)
        if content.content_type == "reply":
            metadata.setdefault("in_reply_to_tweet_id", content.metadata.get("in_reply_to_tweet_id"))

        return ChannelPayload(
            workspace_id=content.workspace_id,
            channel=self.channel,
            body=text,
            title=content.title,
            metadata=metadata,
        )
