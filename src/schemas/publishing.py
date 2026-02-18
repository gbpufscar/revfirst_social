"""Pydantic schemas for publishing endpoints."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class PublishReplyRequest(BaseModel):
    workspace_id: str = Field(min_length=36, max_length=36)
    text: str = Field(min_length=2, max_length=280)
    in_reply_to_tweet_id: str = Field(min_length=1, max_length=64)
    thread_id: Optional[str] = Field(default=None, max_length=64)
    target_author_id: Optional[str] = Field(default=None, max_length=64)


class PublishPostRequest(BaseModel):
    workspace_id: str = Field(min_length=36, max_length=36)
    text: str = Field(min_length=2, max_length=280)


class PublishResponse(BaseModel):
    workspace_id: str
    action: str
    published: bool
    external_post_id: Optional[str] = None
    status: str
    message: str

