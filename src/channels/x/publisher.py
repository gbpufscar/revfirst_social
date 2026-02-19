"""X publisher adapter.

Maintains compatibility with existing dedicated publish engine by supporting preview-only mode.
"""

from __future__ import annotations

from typing import Optional

from src.channels.base import ChannelPayload, ChannelPublishResult
from src.integrations.x.x_client import XClient, XClientError


class XPublisher:
    channel = "x"

    def __init__(self, *, x_client: Optional[XClient] = None, access_token: Optional[str] = None) -> None:
        self._x_client = x_client
        self._access_token = access_token

    def publish(self, payload: ChannelPayload) -> ChannelPublishResult:
        if self._x_client is None or not self._access_token:
            return ChannelPublishResult(
                channel=self.channel,
                published=False,
                status="preview_only",
                message="X publisher running in preview mode",
                payload={"text": payload.body},
            )

        in_reply_to_tweet_id = payload.metadata.get("in_reply_to_tweet_id")
        try:
            response = self._x_client.create_tweet(
                access_token=self._access_token,
                text=payload.body,
                in_reply_to_tweet_id=(str(in_reply_to_tweet_id) if in_reply_to_tweet_id else None),
            )
            data = response.get("data") if isinstance(response, dict) else {}
            external_id = str(data.get("id")) if isinstance(data, dict) and data.get("id") else None
            return ChannelPublishResult(
                channel=self.channel,
                published=True,
                status="published",
                message="X content published",
                external_id=external_id,
                payload=response if isinstance(response, dict) else {},
            )
        except XClientError as exc:
            return ChannelPublishResult(
                channel=self.channel,
                published=False,
                status="failed",
                message=f"X publish failed: {exc}",
                payload={"error": str(exc)},
            )
