# Operational Validation Log (Single-Workspace Window)

Status: Active (Phase 11 Validation Window)  
Window: 30 days  
Last Updated: 2026-02-18

## How to use

- Fill one entry per day with real production evidence.
- Use data from `/metrics`, DB usage tables, scheduler logs, and Sentry.
- Do not backfill with estimates.

## Daily Entry Template

### Date
- `YYYY-MM-DD`

### Runtime Snapshot
- App version:
- Commit SHA:
- Workspace ID:
- Single-workspace mode:

### Execution Summary
- Total scheduler runs:
- Manual runs triggered:
- Pipelines executed:

### Output Summary
- Replies generated:
- Replies approved:
- Replies published:
- Replies blocked (by reason):
- Daily post generated:
- Daily post published:
- Seeds ingested:

### Quality and Safety
- Brand guard blocks:
- Anti-cringe blocks:
- Rate-limit blocks:
- Publish errors:

### Platform Health
- `/health` status:
- DB health:
- Redis health:
- Critical incidents (count):

### Observability
- New Sentry issues:
- Resolved Sentry issues:
- Metrics anomalies:

### Improvements Applied
- Threshold or config changes:
- Prompt/template updates:
- Operational actions:

### Notes
- Free-text notes for context and decisions.

## Weekly Rollup Template

- Week range:
- Approval rate:
- Guard-block rate:
- Publish failure rate:
- Incident summary:
- Decision: continue / adjust / rollback
