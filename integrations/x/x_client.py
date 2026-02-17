"""X integration client interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class XCandidate:
    post_id: str
    author: str
    text: str
    created_at: str


class XClient:
    def fetch_open_calls(self, limit: int = 50) -> list[XCandidate]:
        """Fetch public opportunities from X."""
        # TODO: implement with official API credentials.
        return []

    def fetch_trends(self) -> list[dict[str, Any]]:
        """Fetch trend signals to enrich candidate ranking."""
        # TODO: implement trend ingestion.
        return []

    def publish_post(self, text: str, reply_to_id: str | None = None) -> str:
        """Publish a post or reply and return platform id."""
        # TODO: implement publish operation.
        raise NotImplementedError("X publish API integration is not implemented yet.")
