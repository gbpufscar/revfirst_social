"""Pydantic schemas for ingestion endpoints."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class OpenCallsRunRequest(BaseModel):
    workspace_id: str = Field(min_length=36, max_length=36)
    max_results: int = Field(default=20, ge=10, le=100)
    query: Optional[str] = Field(default=None, max_length=1024)


class OpenCallsRunResponse(BaseModel):
    workspace_id: str
    fetched: int
    stored_new: int
    stored_updated: int
    ranked: int
    top_opportunity_score: int


class CandidateResponse(BaseModel):
    id: str
    workspace_id: str
    source_tweet_id: str
    author_handle: Optional[str]
    text: str
    intent: str
    opportunity_score: int
    url: Optional[str]
    created_at: str


class CandidateListResponse(BaseModel):
    workspace_id: str
    count: int
    candidates: List[CandidateResponse]

