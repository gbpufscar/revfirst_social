from __future__ import annotations

from src.core.metrics import (
    record_daily_post_published,
    record_publish_error,
    record_replies_generated,
    record_replies_published,
    record_reply_blocked,
    record_seed_used,
    render_prometheus_metrics,
    reset_metrics_for_tests,
)


def test_phase11_business_counters_are_exposed_in_metrics() -> None:
    reset_metrics_for_tests()
    workspace_id = "workspace-abc"

    record_replies_generated(workspace_id=workspace_id, count=3)
    record_replies_published(workspace_id=workspace_id, count=2)
    record_reply_blocked(workspace_id=workspace_id, reason="brand_guard", count=1)
    record_daily_post_published(workspace_id=workspace_id, count=1)
    record_seed_used(workspace_id=workspace_id, count=5)
    record_publish_error(workspace_id=workspace_id, channel="x", count=2)

    payload = render_prometheus_metrics(app_name="revfirst_social", app_version="0.1.0", env="test")

    assert 'revfirst_replies_generated_total{workspace_id="workspace-abc"} 3' in payload
    assert 'revfirst_replies_published_total{workspace_id="workspace-abc"} 2' in payload
    assert 'revfirst_reply_blocked_total{workspace_id="workspace-abc",reason="brand_guard"} 1' in payload
    assert 'revfirst_daily_post_published_total{workspace_id="workspace-abc"} 1' in payload
    assert 'revfirst_seed_used_total{workspace_id="workspace-abc"} 5' in payload
    assert 'revfirst_publish_errors_total{workspace_id="workspace-abc",channel="x"} 2' in payload
