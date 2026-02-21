"""Canonical editorial queue statuses with legacy aliases."""

from __future__ import annotations

from typing import Iterable, Tuple


QUEUE_STATUS_PENDING_REVIEW = "pending_review"
QUEUE_STATUS_APPROVED_SCHEDULED = "approved_scheduled"
QUEUE_STATUS_PUBLISHING = "publishing"
QUEUE_STATUS_PUBLISHED = "published"
QUEUE_STATUS_REJECTED = "rejected"
QUEUE_STATUS_FAILED = "failed"

LEGACY_QUEUE_STATUS_PENDING = "pending"
LEGACY_QUEUE_STATUS_APPROVED = "approved"

PENDING_REVIEW_STATUSES: Tuple[str, ...] = (
    QUEUE_STATUS_PENDING_REVIEW,
    LEGACY_QUEUE_STATUS_PENDING,
)
APPROVED_SCHEDULED_STATUSES: Tuple[str, ...] = (
    QUEUE_STATUS_APPROVED_SCHEDULED,
    LEGACY_QUEUE_STATUS_APPROVED,
)
FINAL_QUEUE_STATUSES = {
    QUEUE_STATUS_PUBLISHED,
    QUEUE_STATUS_REJECTED,
    QUEUE_STATUS_FAILED,
}


def canonicalize_queue_status(status: str | None) -> str:
    normalized = str(status or "").strip().lower()
    if normalized == LEGACY_QUEUE_STATUS_PENDING:
        return QUEUE_STATUS_PENDING_REVIEW
    if normalized == LEGACY_QUEUE_STATUS_APPROVED:
        return QUEUE_STATUS_APPROVED_SCHEDULED
    if normalized:
        return normalized
    return QUEUE_STATUS_PENDING_REVIEW


def is_pending_review_status(status: str | None) -> bool:
    return canonicalize_queue_status(status) == QUEUE_STATUS_PENDING_REVIEW


def is_approved_scheduled_status(status: str | None) -> bool:
    return canonicalize_queue_status(status) == QUEUE_STATUS_APPROVED_SCHEDULED


def canonicalize_statuses(values: Iterable[str]) -> list[str]:
    return [canonicalize_queue_status(value) for value in values]

