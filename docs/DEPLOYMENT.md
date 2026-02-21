# RevFirst_Social Deployment Guide

Status: Active  
Last Updated: 2026-02-20

## Target

- Platform: Coolify
- Environment: production
- Canonical domain: `social.revfirst.cloud`
- App port: `8000`

## App Configuration (Coolify)

- Build Pack: `Dockerfile`
- Base Directory: `/`
- Dockerfile Location: `/deploy/Dockerfile`
- Ports Exposes: `8000`
- Domain: `https://social.revfirst.cloud`

## Required Production Environment Variables

- `PORT=8000`
- `ENV=production`
- `SECRET_KEY=...`
- `TOKEN_ENCRYPTION_KEY=...`
- `DATABASE_URL=postgres://.../revfirst_social`
- `REDIS_URL=redis://...:6379/0`
- `X_CLIENT_ID=...`
- `X_CLIENT_SECRET=...`
- `X_REDIRECT_URI=https://social.revfirst.cloud/integrations/x/oauth/callback`
- `X_AUTHORIZE_URL=https://twitter.com/i/oauth2/authorize`
- `X_USERS_ME_URL=https://api.twitter.com/2/users/me`
- `X_OAUTH_STATE_TTL_SECONDS=600`
- `X_REQUIRED_PUBLISH_SCOPE=tweet.write`
- `TELEGRAM_WEBHOOK_SECRET=...`
- `TELEGRAM_BOT_TOKEN=...` (required for proactive alert delivery)
- `TELEGRAM_ADMINS_FILE_PATH=/run/secrets/telegram_admins.yaml`
- `APP_PUBLIC_BASE_URL=https://social.revfirst.cloud`
- `PUBLISHING_DIRECT_API_ENABLED=false`
- `PUBLISHING_DIRECT_API_INTERNAL_KEY=...`
- `STABILITY_GUARD_SCHEDULER_CHECKS_ENABLED=true`
- `STABILITY_AUTO_CONTAINMENT_ON_CRITICAL=true`
- `STABILITY_KILL_SWITCH_ENABLED=true`
- `STABILITY_KILL_SWITCH_CRITERIA_THRESHOLD=3`
- `STABILITY_KILL_SWITCH_TTL_SECONDS=3600`
- `STABILITY_KILL_SWITCH_ACK_TTL_SECONDS=21600`
- `METRICS_ENABLED=true`
- `IP_RATE_LIMIT_ENABLED=true`
- `IP_RATE_LIMIT_REQUESTS_PER_WINDOW=120`
- `IP_RATE_LIMIT_WINDOW_SECONDS=60`

Optional observability:
- `SENTRY_DSN=...`
- `SENTRY_TRACES_SAMPLE_RATE=0.05`

## DNS (Hostinger)

For subdomain setup:
- Type: `A`
- Host: `social`
- Value: server public IPv4
- TTL: `300`

Do not use child nameserver setup for this subdomain scenario.

## Deploy Procedure

0. Block deploy from dirty worktree:
   - `git status --short` must be empty.
   - If not empty, stop and commit or stash before any release.
1. Confirm source branch is `main`.
2. Save app config and environment variables.
3. Validate secrets before redeploy:
   - `bash scripts/check_production_secrets.sh /path/to/production.env`
4. Redeploy app in Coolify.
5. Wait for container healthcheck:
   - `GET http://localhost:8000/health`
   - status must be `healthy`.
6. Validate public endpoints.

## Scheduler Service (systemd)

- Unit file: `deploy/systemd/revfirst_social.service`
- Canonical entrypoint:
  - `ExecStart=/usr/bin/python3 -m src.orchestrator.manager`
- Legacy entrypoint `-m orchestrator.manager` is deprecated and must not be used in production.

## Post-Deploy Validation

```bash
curl -ik https://social.revfirst.cloud/health
curl -ik https://social.revfirst.cloud/version
curl -ik https://social.revfirst.cloud/metrics | head -n 30
```

Expected:
- All endpoints return `200`.
- `/health` reports DB and Redis `ok=true`.
- Telegram `/status` must show `mode` and match planned release mode.

OAuth official account validation (X):

```bash
curl -sS -H "Authorization: Bearer <OWNER_OR_ADMIN_JWT>" \
  https://social.revfirst.cloud/integrations/x/oauth/status/<WORKSPACE_ID>
```

Expected:
- `connected=true`
- `has_publish_scope=true`
- `publish_ready=true`
- `account_user_id` and `account_username` populated

## Common Failure: `503 no available server`

Typical causes:
- malformed domain rule labels in Coolify.
- missing or wrong service port (`8000` expected).
- domain conflict registered on another Coolify resource.

Fix:
1. Remove domain, save, re-add domain with scheme, save.
2. Confirm host-based labels are correct.
3. Restart proxy.
4. Redeploy app.
