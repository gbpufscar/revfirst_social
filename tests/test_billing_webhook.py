from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

import src.api.main as api_main
from src.billing.plans import load_plans
from src.core.config import get_settings
from src.storage.db import Base, get_session, load_models
from src.storage.models import StripeEvent, Workspace


def _signature(payload: bytes, secret: str, timestamp: int) -> str:
    message = f"{timestamp}.{payload.decode('utf-8')}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def test_stripe_webhook_is_idempotent(monkeypatch) -> None:
    monkeypatch.setenv("PLANS_FILE_PATH", "config/plans.yaml")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test_secret_1234567890")
    get_settings.cache_clear()
    load_plans.cache_clear()

    load_models()
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    with session_factory() as seed_session:
        workspace = Workspace(
            id=str(uuid.uuid4()),
            name="billing-webhook",
            plan="free",
            stripe_customer_id="cus_test_123",
            subscription_status="inactive",
        )
        seed_session.add(workspace)
        seed_session.commit()

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    api_main.app.dependency_overrides[get_session] = override_get_session
    try:
        client = TestClient(api_main.app)
        event_payload = {
            "id": "evt_test_001",
            "type": "customer.subscription.created",
            "data": {
                "object": {
                    "id": "sub_test_001",
                    "customer": "cus_test_123",
                    "status": "active",
                    "items": {
                        "data": [
                            {
                                "price": {
                                    "lookup_key": "pro",
                                }
                            }
                        ]
                    },
                }
            },
        }
        payload_bytes = json.dumps(event_payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        timestamp = int(time.time())
        header = _signature(payload_bytes, "whsec_test_secret_1234567890", timestamp)

        first_response = client.post(
            "/billing/webhook",
            content=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "Stripe-Signature": header,
            },
        )
        assert first_response.status_code == 200
        assert first_response.json()["status"] == "processed"
        assert first_response.json()["duplicate"] is False

        second_response = client.post(
            "/billing/webhook",
            content=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "Stripe-Signature": header,
            },
        )
        assert second_response.status_code == 200
        assert second_response.json()["status"] == "duplicate"
        assert second_response.json()["duplicate"] is True

        with session_factory() as verify_session:
            updated_workspace = verify_session.scalar(
                select(Workspace).where(Workspace.stripe_customer_id == "cus_test_123")
            )
            assert updated_workspace is not None
            assert updated_workspace.plan == "pro"
            assert updated_workspace.subscription_status == "active"

            events = verify_session.scalars(select(StripeEvent).where(StripeEvent.event_id == "evt_test_001")).all()
            assert len(events) == 1
            assert events[0].status == "processed"
    finally:
        api_main.app.dependency_overrides.clear()
        get_settings.cache_clear()

