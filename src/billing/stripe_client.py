"""Stripe webhook parsing and signature verification helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class StripeSignatureData:
    timestamp: int
    signatures: List[str]


class StripeWebhookError(ValueError):
    """Raised when webhook payload/signature is invalid."""


def parse_stripe_signature_header(signature_header: str) -> StripeSignatureData:
    timestamp: Optional[int] = None
    signatures: List[str] = []

    for part in signature_header.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError as exc:
                raise StripeWebhookError("Invalid Stripe signature timestamp") from exc
        elif key == "v1":
            signatures.append(value)

    if timestamp is None or not signatures:
        raise StripeWebhookError("Invalid Stripe signature header")

    return StripeSignatureData(timestamp=timestamp, signatures=signatures)


def verify_stripe_signature(
    *,
    payload: bytes,
    signature_header: str,
    webhook_secret: str,
    tolerance_seconds: int = 300,
    now: Optional[datetime] = None,
) -> None:
    if not webhook_secret:
        raise StripeWebhookError("Stripe webhook secret is not configured")

    data = parse_stripe_signature_header(signature_header)
    current_time = now or datetime.now(timezone.utc)
    age_seconds = abs(int(current_time.timestamp()) - data.timestamp)
    if age_seconds > tolerance_seconds:
        raise StripeWebhookError("Stripe signature timestamp outside tolerance window")

    signed_payload = f"{data.timestamp}.{payload.decode('utf-8')}".encode("utf-8")
    expected = hmac.new(
        webhook_secret.encode("utf-8"),
        signed_payload,
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not any(hmac.compare_digest(expected, candidate) for candidate in data.signatures):
        raise StripeWebhookError("Stripe signature mismatch")


def parse_stripe_event(payload: bytes) -> Dict[str, Any]:
    try:
        event = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise StripeWebhookError("Invalid Stripe JSON payload") from exc

    if not isinstance(event, dict):
        raise StripeWebhookError("Stripe payload must be a JSON object")
    if "id" not in event or "type" not in event:
        raise StripeWebhookError("Stripe payload missing required fields: id/type")
    return event

