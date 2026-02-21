from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.control.queue_executor import execute_approved_queue_items
from src.control.services import create_queue_item
from src.storage.models import ApprovalQueueItem
from tests.control.conftest import create_control_test_context, teardown_control_test_context


def test_execute_due_scheduled_items_publishes_once(monkeypatch, tmp_path) -> None:
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
            item = create_queue_item(
                session,
                workspace_id=context.workspace_id,
                item_type="post",
                content_text="Due scheduled post.",
                source_kind="manual_test",
                source_ref_id="scheduled-post-1",
                intent="daily_post",
                opportunity_score=100,
                idempotency_key="test-queue-executor-due-1",
            )
            item.status = "approved_scheduled"
            item.approved_by_user_id = context.owner_user_id
            item.approved_at = datetime.now(timezone.utc) - timedelta(minutes=5)
            item.scheduled_for = datetime.now(timezone.utc) - timedelta(minutes=2)
            item.publish_window_key = "20260221-0730"
            session.commit()
            queue_id = item.id

            first = execute_approved_queue_items(
                session,
                workspace_id=context.workspace_id,
                x_client=context.fake_x,
                dry_run=False,
                owner_override=False,
                due_only=True,
                now_utc=datetime.now(timezone.utc),
            )
            second = execute_approved_queue_items(
                session,
                workspace_id=context.workspace_id,
                x_client=context.fake_x,
                dry_run=False,
                owner_override=False,
                due_only=True,
                now_utc=datetime.now(timezone.utc),
            )

            row = session.scalar(
                select(ApprovalQueueItem).where(
                    ApprovalQueueItem.workspace_id == context.workspace_id,
                    ApprovalQueueItem.id == queue_id,
                )
            )
            assert row is not None
            assert row.status == "published"
            assert row.published_post_id == "tweet-1"

        assert first["published"] == 1
        assert first["failed"] == 0
        assert second["published"] == 0
    finally:
        teardown_control_test_context()


def test_execute_due_scheduled_items_skips_future(monkeypatch, tmp_path) -> None:
    context = create_control_test_context(monkeypatch, tmp_path)
    try:
        with context.session_factory() as session:
            item = create_queue_item(
                session,
                workspace_id=context.workspace_id,
                item_type="post",
                content_text="Future scheduled post.",
                source_kind="manual_test",
                source_ref_id="scheduled-post-future-1",
                intent="daily_post",
                opportunity_score=100,
                idempotency_key="test-queue-executor-future-1",
            )
            item.status = "approved_scheduled"
            item.approved_by_user_id = context.owner_user_id
            item.approved_at = datetime.now(timezone.utc)
            item.scheduled_for = datetime.now(timezone.utc) + timedelta(hours=2)
            item.publish_window_key = "20260221-1630"
            session.commit()

            result = execute_approved_queue_items(
                session,
                workspace_id=context.workspace_id,
                x_client=context.fake_x,
                dry_run=False,
                owner_override=False,
                due_only=True,
                now_utc=datetime.now(timezone.utc),
            )
            refreshed = session.scalar(
                select(ApprovalQueueItem).where(
                    ApprovalQueueItem.workspace_id == context.workspace_id,
                    ApprovalQueueItem.id == item.id,
                )
            )
            assert refreshed is not None
            assert refreshed.status == "approved_scheduled"

        assert result["published"] == 0
        assert result["scheduled_pending"] == 1
    finally:
        teardown_control_test_context()

