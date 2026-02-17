# Architecture

## Layers
- Integrations: X and Telegram IO.
- Agents: classification, scoring, writing, validation.
- Pipelines: ordered jobs for ingest to reporting.
- Orchestrator: scheduling, routing, retries.
- Data: local SQLite and JSONL artifacts.

## Design Principles
- Stateless pipeline steps whenever possible.
- Explicit contracts through JSON schemas.
- Idempotent writes for scheduled jobs.
- Full traceability for approvals and publishes.
