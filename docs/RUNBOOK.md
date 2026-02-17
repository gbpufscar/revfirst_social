# Runbook

## Daily Operations
- Run ingest, ranking, proposal, and validation pipelines.
- Review and approve queue.
- Publish approved items.
- Fetch metrics and store report snapshots.

## Incident Playbook
- If integration fails: retry with backoff and mark pipeline degraded.
- If quality checks fail: stop publish and switch to manual review.
- If scheduler fails: run manager in manual mode and inspect logs.

## Recovery
- Restore from latest data snapshots.
- Re-run idempotent pipelines in sequence.
