"""Route pipeline execution by name."""

from __future__ import annotations

from pipelines import (
    approve_queue,
    create_daily_post,
    execute_posts,
    fetch_metrics,
    ingest_open_calls,
    ingest_trends,
    monitor_leads,
    propose_replies,
    rank_candidates,
    thread_hijack,
    weekly_report,
)


ROUTES = {
    "ingest_open_calls": ingest_open_calls.run,
    "ingest_trends": ingest_trends.run,
    "rank_candidates": rank_candidates.run,
    "propose_replies": propose_replies.run,
    "approve_queue": approve_queue.run,
    "execute_posts": execute_posts.run,
    "create_daily_post": create_daily_post.run,
    "thread_hijack": thread_hijack.run,
    "monitor_leads": monitor_leads.run,
    "fetch_metrics": fetch_metrics.run,
    "weekly_report": weekly_report.run,
}


def run_pipeline(name: str) -> dict:
    handler = ROUTES.get(name)
    if handler is None:
        return {"status": "error", "pipeline": name, "reason": "unknown_pipeline"}
    return handler()
