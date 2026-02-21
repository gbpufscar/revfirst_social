# RevFirst_Social Runbook

Status: Active  
Last Updated: 2026-02-20

## 1. Production Base URL

- Canonical production URL: `https://social.revfirst.cloud`
- Internal fallback URL (Coolify generated): keep only for emergency checks.

## 2. Daily Operations

1. Validate platform health.
2. Run ingestion and candidate ranking.
3. Review and approve queue.
4. Publish approved items.
5. Capture metrics snapshot.
6. Review Sentry issues and close/triage new alerts.

Scheduler command policy:
- Canonical command: `python -m src.orchestrator.manager`
- Do not use legacy command under the `orchestrator` package.

Operational mode policy:
- Check current mode via Telegram: `/mode`
- Change mode (owner/admin): `/mode set <manual|semi_autonomous|autonomous_limited|containment> [confirm]`
- `manual` and `containment` disable scheduler execution.

## 3. Quick Health Checks

```bash
curl -ik https://social.revfirst.cloud/health
curl -ik https://social.revfirst.cloud/version
curl -ik https://social.revfirst.cloud/metrics | head -n 30
```

Expected:
- `/health` returns `200` and DB/Redis `ok=true`.
- `/version` returns app metadata.
- `/metrics` returns Prometheus text output.

## 4. Deploy / Redeploy (Coolify)

0. Never deploy from dirty git worktree:
   - `git status --short` must return empty output.
   - If not empty, stop the release and commit/stash first.
1. Confirm app source branch is `main`.
2. Confirm domain is `social.revfirst.cloud`.
3. Confirm app runtime port is `8000`.
4. Validate production secrets file:
   - `bash scripts/check_production_secrets.sh /path/to/production.env`
5. Click `Redeploy`.
6. Wait for:
   - `Rolling update started`
   - `Healthcheck status: healthy`
   - `Rolling update completed`
7. Run Quick Health Checks.

## 5. Database Migrations in Production

Run inside app container terminal:

```bash
cd /app
python -m alembic current
python -m alembic upgrade head
python -m alembic current
```

Expected:
- Final `current` must be latest revision.
- No migration error in output.

## 6. Observability Routine

- Metrics endpoint: `GET /metrics`.
- Sentry project enabled with:
  - `SENTRY_DSN`
  - `SENTRY_TRACES_SAMPLE_RATE`
- X OAuth status endpoint:
  - `GET /integrations/x/oauth/status/{workspace_id}`
- Rate limit headers expected in production responses:
  - `x-rate-limit-limit`
  - `x-rate-limit-remaining`
  - `x-rate-limit-reset`
  - `x-request-id`

## 7. Official X Account Verification

Run after OAuth reconnect or credential rotation:

```bash
curl -sS -H "Authorization: Bearer <OWNER_OR_ADMIN_JWT>" \
  https://social.revfirst.cloud/integrations/x/oauth/status/<WORKSPACE_ID>
```

Acceptance criteria:
- `connected=true`
- `has_publish_scope=true`
- `publish_ready=true`
- `account_user_id` filled
- `account_username` matches the expected official handle

DB cross-check in production:

```bash
cd /app
python -m alembic current
python -m alembic upgrade 20260220_0009
python -m alembic current
psql "$DATABASE_URL" -c "SELECT workspace_id,account_user_id,account_username,scope,revoked_at FROM x_oauth_tokens WHERE workspace_id='<WORKSPACE_ID>' AND provider='x';"
```

Expected:
- revision includes `20260220_0009`
- `account_user_id` and `account_username` are not null
- `scope` contains `tweet.write`

## 8. Incident Playbook

### 8.1 `503 no available server`

Likely proxy routing issue (not app logic).

Actions:
1. Verify app health inside container (`127.0.0.1:8000/health`).
2. Verify Coolify domain labels:
   - `Host(\`social.revfirst.cloud\`)`
   - `PathPrefix(\`/\`)`
   - service port `8000`
3. Remove and re-add domain in Coolify if labels are malformed.
4. Restart proxy (`Servers -> localhost -> Proxy -> Restart`).
5. Redeploy app and re-run Quick Health Checks.

### 8.2 `health` degraded (`503`)

Actions:
1. Check DB URL and connectivity.
2. Check Redis URL and connectivity.
3. Validate dependent services status in Coolify.
4. Re-run health checks.

### 8.3 Scheduler failures

Actions:
1. Run one cycle manually:
   - `python -m src.orchestrator.manager`
2. Inspect scheduler logs for workspace lock/tenant context issues.
3. Retry after lock TTL if lock contention is detected.

## 9. Recovery

1. Restore data from latest snapshot/backup.
2. Re-run the canonical scheduler cycle until backlog normalizes:
   - `python -m src.orchestrator.manager --limit 1`
3. Re-validate:
   - `/health`
   - `/version`
   - `/metrics`
4. Confirm no critical unresolved Sentry issue remains open.
