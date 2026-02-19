"""Canonical content object contract for multichannel routing."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


ContentType = Literal[
    "short_post",
    "reply",
    "newsletter",
    "blog_article",
    "ig_caption",
    "thread",
]

ChannelTarget = Literal["x", "email", "blog", "instagram"]


class ContentObject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=36, max_length=36)
    content_type: ContentType
    title: Optional[str] = Field(default=None, min_length=3, max_length=180)
    body: str = Field(min_length=5)
    cta: Optional[str] = Field(default=None, max_length=180)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    channel_targets: List[ChannelTarget] = Field(default_factory=lambda: ["x"])
    source_agent: Optional[str] = Field(default=None, max_length=64)

    @field_validator("channel_targets")
    @classmethod
    def _normalize_channel_targets(cls, targets: List[ChannelTarget]) -> List[ChannelTarget]:
        if not targets:
            return ["x"]
        normalized: List[ChannelTarget] = []
        seen = set()
        for target in targets:
            label = str(target).strip().lower()
            if label not in {"x", "email", "blog", "instagram"}:
                continue
            if label in seen:
                continue
            seen.add(label)
            normalized.append(label)
        return normalized or ["x"]
