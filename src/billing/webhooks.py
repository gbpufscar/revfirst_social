"""Stripe webhook endpoint with idempotent processing."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.billing.plans import load_plans
from src.billing.stripe_client import (
    StripeWebhookError,
    parse_stripe_event,
    verify_stripe_signature,
)
from src.core.config import get_settings
from src.schemas.billing import StripeWebhookResponse
from src.storage.db import get_session
from src.storage.models import StripeEvent, Subscription, Workspace


router = APIRouter(prefix="/billing", tags=["billing"])


def _as_json(payload: bytes) -> str:
    return payload.decode("utf-8")


def _find_workspace_by_customer(session: Session, customer_id: str) -> Optional[Workspace]:
    return session.scalar(select(Workspace).where(Workspace.stripe_customer_id == customer_id))


def _resolve_plan_name_from_subscription(subscription: Dict[str, Any], fallback: str) -> str:
    items = subscription.get("items") or {}
    data = items.get("data") or []
    first_item = data[0] if isinstance(data, list) and data else {}
    price = first_item.get("price") or {}

    lookup_key = price.get("lookup_key")
    if isinstance(lookup_key, str) and lookup_key:
        return lookup_key

    nickname = price.get("nickname")
    if isinstance(nickname, str) and nickname:
        return nickname.lower().replace(" ", "_")

    metadata = subscription.get("metadata") or {}
    metadata_plan = metadata.get("plan")
    if isinstance(metadata_plan, str) and metadata_plan:
        return metadata_plan

    return fallback


def _upsert_subscription(
    session: Session,
    *,
    workspace_id: str,
    customer_id: str,
    subscription_id: str,
    plan_name: str,
    subscription_status: str,
) -> None:
    record = session.scalar(select(Subscription).where(Subscription.workspace_id == workspace_id))
    if record is None:
        record = Subscription(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            stripe_customer_id=customer_id,
            stripe_subscription_id=subscription_id,
            plan=plan_name,
            status=subscription_status,
        )
        session.add(record)
    else:
        record.stripe_customer_id = customer_id
        record.stripe_subscription_id = subscription_id
        record.plan = plan_name
        record.status = subscription_status
        record.updated_at = datetime.now(timezone.utc)


def _insert_stripe_event(
    session: Session,
    *,
    event_id: str,
    event_type: str,
    payload_json: str,
) -> Tuple[Optional[StripeEvent], bool]:
    stripe_event = StripeEvent(
        id=str(uuid.uuid4()),
        event_id=event_id,
        event_type=event_type,
        status="received",
        payload_json=payload_json,
    )
    session.add(stripe_event)
    try:
        session.commit()
        return stripe_event, False
    except IntegrityError:
        session.rollback()
        return None, True


def _mark_event_failed(session: Session, event_id: str, error_message: str) -> None:
    stripe_event = session.scalar(select(StripeEvent).where(StripeEvent.event_id == event_id))
    if stripe_event is None:
        return
    stripe_event.status = "failed"
    stripe_event.error_message = error_message[:255]
    stripe_event.processed_at = datetime.now(timezone.utc)
    session.commit()


def _apply_subscription_event(
    session: Session,
    *,
    stripe_event: StripeEvent,
    event_type: str,
    payload: Dict[str, Any],
) -> Tuple[str, str]:
    data = payload.get("data") or {}
    subscription = data.get("object") or {}
    if not isinstance(subscription, dict):
        return "ignored", "Subscription payload is invalid"

    customer_id = subscription.get("customer")
    subscription_id = subscription.get("id")
    subscription_status = subscription.get("status") or "inactive"
    if not isinstance(customer_id, str) or not customer_id:
        return "ignored", "Subscription payload missing customer"
    if not isinstance(subscription_id, str) or not subscription_id:
        return "ignored", "Subscription payload missing id"

    workspace = _find_workspace_by_customer(session, customer_id)
    if workspace is None:
        return "ignored", "No workspace linked to Stripe customer"

    configured_plans = load_plans()
    resolved_plan = _resolve_plan_name_from_subscription(subscription, workspace.plan)
    if resolved_plan not in configured_plans:
        resolved_plan = workspace.plan

    workspace.plan = resolved_plan
    workspace.subscription_status = str(subscription_status)

    _upsert_subscription(
        session,
        workspace_id=workspace.id,
        customer_id=customer_id,
        subscription_id=subscription_id,
        plan_name=resolved_plan,
        subscription_status=str(subscription_status),
    )

    stripe_event.workspace_id = workspace.id
    if event_type == "customer.subscription.deleted":
        workspace.subscription_status = "canceled"
        if workspace.plan not in configured_plans:
            workspace.plan = "free"

    return "processed", "Subscription event applied"


def _apply_payment_failed_event(
    session: Session,
    *,
    stripe_event: StripeEvent,
    payload: Dict[str, Any],
) -> Tuple[str, str]:
    data = payload.get("data") or {}
    invoice = data.get("object") or {}
    if not isinstance(invoice, dict):
        return "ignored", "Invoice payload is invalid"

    customer_id = invoice.get("customer")
    if not isinstance(customer_id, str) or not customer_id:
        return "ignored", "Invoice payload missing customer"

    workspace = _find_workspace_by_customer(session, customer_id)
    if workspace is None:
        return "ignored", "No workspace linked to Stripe customer"

    workspace.subscription_status = "past_due"
    stripe_event.workspace_id = workspace.id
    return "processed", "Payment failure applied"


def process_stripe_event(
    session: Session,
    *,
    event: Dict[str, Any],
    payload_bytes: bytes,
) -> StripeWebhookResponse:
    event_id = str(event["id"])
    event_type = str(event["type"])

    stripe_event, duplicate = _insert_stripe_event(
        session,
        event_id=event_id,
        event_type=event_type,
        payload_json=_as_json(payload_bytes),
    )
    if duplicate:
        return StripeWebhookResponse(
            status="duplicate",
            duplicate=True,
            event_id=event_id,
            event_type=event_type,
            message="Event already processed",
        )
    if stripe_event is None:  # pragma: no cover
        raise RuntimeError("Failed to persist Stripe event")

    try:
        if event_type in {
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
        }:
            final_status, message = _apply_subscription_event(
                session,
                stripe_event=stripe_event,
                event_type=event_type,
                payload=event,
            )
        elif event_type == "invoice.payment_failed":
            final_status, message = _apply_payment_failed_event(
                session,
                stripe_event=stripe_event,
                payload=event,
            )
        else:
            final_status, message = "ignored", "Unsupported Stripe event type"

        stripe_event.status = final_status
        stripe_event.error_message = None if final_status != "ignored" else message[:255]
        stripe_event.processed_at = datetime.now(timezone.utc)
        session.commit()
        return StripeWebhookResponse(
            status=final_status,
            duplicate=False,
            event_id=event_id,
            event_type=event_type,
            message=message,
        )
    except Exception as exc:
        session.rollback()
        _mark_event_failed(session, event_id, str(exc))
        return StripeWebhookResponse(
            status="failed",
            duplicate=False,
            event_id=event_id,
            event_type=event_type,
            message="Processing failed",
        )


@router.post("/webhook", response_model=StripeWebhookResponse)
async def stripe_webhook(
    request: Request,
    session: Session = Depends(get_session),
) -> StripeWebhookResponse:
    settings = get_settings()
    if not settings.stripe_webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe webhook secret is not configured",
        )

    payload_bytes = await request.body()
    signature_header = request.headers.get("stripe-signature", "")
    try:
        verify_stripe_signature(
            payload=payload_bytes,
            signature_header=signature_header,
            webhook_secret=settings.stripe_webhook_secret,
            tolerance_seconds=settings.stripe_signature_tolerance_seconds,
        )
        event = parse_stripe_event(payload_bytes)
    except StripeWebhookError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return process_stripe_event(session, event=event, payload_bytes=payload_bytes)

