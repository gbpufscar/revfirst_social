"""JSON contracts for domain agents."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ReplyDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=36, max_length=36)
    source_tweet_id: Optional[str] = Field(default=None, max_length=64)
    intent: str = Field(min_length=1, max_length=32)
    text: str = Field(min_length=5, max_length=280)
    confidence: int = Field(ge=0, le=100)
    rationale: str = Field(min_length=5, max_length=300)
    tags: List[str] = Field(default_factory=list)


class BrandConsistencyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    score: int = Field(ge=0, le=100)
    violations: List[str] = Field(default_factory=list)
    normalized_text: str = Field(default="")


class CringeCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cringe: bool
    risk_score: int = Field(ge=0, le=100)
    flags: List[str] = Field(default_factory=list)


class ThreadDetectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    should_hijack: bool
    score: int = Field(ge=0, le=100)
    context_type: str = Field(min_length=1, max_length=32)
    reasons: List[str] = Field(default_factory=list)


class LeadSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=36, max_length=36)
    source_tweet_id: str = Field(min_length=1, max_length=64)
    author_handle: Optional[str] = Field(default=None, max_length=64)
    lead_type: str = Field(min_length=1, max_length=32)
    lead_score: int = Field(ge=0, le=100)
    signals: List[str] = Field(default_factory=list)
    watch_days: int = Field(ge=1, le=30, default=7)

