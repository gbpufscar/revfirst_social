"""Entry-point manager for pipeline orchestration."""

from __future__ import annotations

from orchestrator.task_router import run_pipeline


def main() -> None:
    for pipeline in [
        "ingest_open_calls",
        "ingest_trends",
        "rank_candidates",
        "propose_replies",
        "approve_queue",
        "execute_posts",
    ]:
        run_pipeline(pipeline)


if __name__ == "__main__":
    main()
