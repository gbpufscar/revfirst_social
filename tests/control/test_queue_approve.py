from __future__ import annotations

from sqlalchemy import select

import src.channels.blog.publisher as blog_publisher_module
import src.channels.email.publisher as email_publisher_module
from src.control.services import create_queue_item
from src.storage.models import ApprovalQueueItem
from tests.control.conftest import create_control_test_context, teardown_control_test_context


def test_queue_and_approve_publish_reply(monkeypatch, tmp_path) -> None:
    context = create_control_test_context(monkeypatch, tmp_path)
    try:
        manual_token = context.client.post(
            "/integrations/x/oauth/token/manual",
            json={
                "workspace_id": context.workspace_id,
                "access_token": "phase12-x-access-token",
                "refresh_token": "phase12-x-refresh-token",
                "expires_in": 3600,
                "scope": "tweet.read tweet.write users.read",
            },
            headers={"Authorization": f"Bearer {context.access_token}"},
        )
        assert manual_token.status_code == 200

        with context.session_factory() as session:
            queue_item = create_queue_item(
                session,
                workspace_id=context.workspace_id,
                item_type="reply",
                content_text="Practical take. Founders win when replies are specific and measurable.",
                source_kind="manual_test",
                source_ref_id="candidate-1",
                intent="open_call",
                opportunity_score=80,
                idempotency_key="test-queue-approve-1",
                metadata={
                    "in_reply_to_tweet_id": "199001",
                    "thread_id": "thread-199001",
                    "target_author_id": "author-42",
                },
            )
            queue_id = queue_item.id

        queue_response = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 4001,
                "message": {
                    "message_id": 901,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": "/queue",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert queue_response.status_code == 200
        queue_payload = queue_response.json()
        assert queue_payload["accepted"] is True
        assert queue_payload["data"]["count"] == 1
        assert queue_payload["data"]["items"][0]["id"] == queue_id

        approve_response = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 4002,
                "message": {
                    "message_id": 902,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": f"/approve {queue_id}",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert approve_response.status_code == 200
        approve_payload = approve_response.json()
        assert approve_payload["accepted"] is True
        assert approve_payload["message"] == "approved_and_published"
        assert approve_payload["data"]["external_post_id"] == "tweet-1"

        with context.session_factory() as session:
            row = session.scalar(
                select(ApprovalQueueItem).where(
                    ApprovalQueueItem.workspace_id == context.workspace_id,
                    ApprovalQueueItem.id == queue_id,
                )
            )
            assert row is not None
            assert row.status == "published"
            assert row.published_post_id == "tweet-1"
    finally:
        teardown_control_test_context()


class _FakeResendClient:
    def __init__(self) -> None:
        self.counter = 0

    def send_email(self, *, from_address: str, to: list[str], subject: str, text: str, tags=None):
        del from_address, to, subject, text, tags
        self.counter += 1
        return {"id": f"email-{self.counter}"}


class _FakeBlogWebhookClient:
    def __init__(self) -> None:
        self.counter = 0

    def publish(self, *, title: str, markdown: str, workspace_id: str, metadata=None):
        del title, markdown, workspace_id, metadata
        self.counter += 1
        return {"id": f"blog-{self.counter}"}


def test_queue_and_approve_publish_email(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "noreply@revfirst.cloud")
    monkeypatch.setenv("EMAIL_DEFAULT_RECIPIENTS", "ops@revfirst.io")
    fake_resend = _FakeResendClient()
    monkeypatch.setattr(email_publisher_module, "get_resend_client", lambda: fake_resend)

    context = create_control_test_context(monkeypatch, tmp_path)
    try:
        with context.session_factory() as session:
            queue_item = create_queue_item(
                session,
                workspace_id=context.workspace_id,
                item_type="email",
                content_text="Daily founder digest content",
                source_kind="manual_test",
                source_ref_id="daily-post-1",
                intent="daily_post",
                opportunity_score=100,
                idempotency_key="test-queue-email-approve-1",
                metadata={
                    "subject": "Daily founder digest",
                    "recipients": ["ops@revfirst.io"],
                },
            )
            queue_id = queue_item.id

        approve_response = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 4102,
                "message": {
                    "message_id": 912,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": f"/approve {queue_id}",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert approve_response.status_code == 200
        approve_payload = approve_response.json()
        assert approve_payload["accepted"] is True
        assert approve_payload["message"] == "approved_and_published"
        assert approve_payload["data"]["external_post_id"] == "email-1"

        with context.session_factory() as session:
            row = session.scalar(
                select(ApprovalQueueItem).where(
                    ApprovalQueueItem.workspace_id == context.workspace_id,
                    ApprovalQueueItem.id == queue_id,
                )
            )
            assert row is not None
            assert row.status == "published"
            assert row.published_post_id == "email-1"
    finally:
        teardown_control_test_context()


def test_queue_and_approve_publish_blog(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BLOG_WEBHOOK_URL", "https://blog-webhook.local/publish")
    fake_blog = _FakeBlogWebhookClient()
    monkeypatch.setattr(blog_publisher_module, "get_blog_webhook_client", lambda: fake_blog)

    context = create_control_test_context(monkeypatch, tmp_path)
    try:
        with context.session_factory() as session:
            queue_item = create_queue_item(
                session,
                workspace_id=context.workspace_id,
                item_type="blog",
                content_text="## Weekly field notes\n\nDirect positioning keeps conversations qualified.",
                source_kind="manual_test",
                source_ref_id="daily-post-blog-1",
                intent="daily_post",
                opportunity_score=100,
                idempotency_key="test-queue-blog-approve-1",
                metadata={
                    "title": "Weekly field notes",
                },
            )
            queue_id = queue_item.id

        approve_response = context.client.post(
            f"/control/telegram/webhook/{context.workspace_id}",
            json={
                "update_id": 4202,
                "message": {
                    "message_id": 922,
                    "chat": {"id": 7001},
                    "from": {"id": 90001},
                    "text": f"/approve {queue_id}",
                },
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": "phase12-secret"},
        )
        assert approve_response.status_code == 200
        approve_payload = approve_response.json()
        assert approve_payload["accepted"] is True
        assert approve_payload["message"] == "approved_and_published"
        assert approve_payload["data"]["external_post_id"] == "blog-1"

        with context.session_factory() as session:
            row = session.scalar(
                select(ApprovalQueueItem).where(
                    ApprovalQueueItem.workspace_id == context.workspace_id,
                    ApprovalQueueItem.id == queue_id,
                )
            )
            assert row is not None
            assert row.status == "published"
            assert row.published_post_id == "blog-1"
    finally:
        teardown_control_test_context()
