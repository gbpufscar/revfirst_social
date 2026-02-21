# RevFirst_Social

RevFirst_Social is the conversation-driven growth engine for RevFirst.

## Mission
Insert RevFirst into the right builder conversations at the right time, with the right message.

## Foundation Scope (Phase 1)
- FastAPI foundation with health/version endpoints
- PostgreSQL and Redis connectivity
- Alembic migration pipeline
- Structured JSON logging
- Dockerized local stack (app + postgres + redis)
- Basic CI (lint, test, docker build)

## Project Structure (Current)
- `src/api`: API entrypoints and HTTP surface
- `src/core`: shared config and logger setup
- `src/orchestrator`: canonical scheduler/orchestration runtime
- `src/storage`: database and redis clients
- `migrations`: Alembic migration scripts
- `deploy`: Docker and docker-compose definitions
- `tests`: automated tests
- `docs`: canonical and implementation documentation

## Local Run (Docker)
```bash
make up
```

API endpoints:
- `GET http://localhost:${APP_PORT:-18000}/health`
- `GET http://localhost:${APP_PORT:-18000}/version`
- `GET http://localhost:${APP_PORT:-18000}/metrics`

Basic load test:
```bash
make loadtest
```

Canonical scheduler command:
```bash
python -m src.orchestrator.manager
```

## Production
- Canonical API base URL: `https://social.revfirst.cloud`
- Operational endpoints:
  - `GET /health`
  - `GET /version`
  - `GET /metrics`

## Docs
- Canonical authority: `docs/PROJECT_CANONICAL.md`
- Execution roadmap: `docs/MASTER_IMPLEMENTATION_PLAN.md`
- Observability baseline: `docs/OBSERVABILITY.md`
- Operations runbook: `docs/RUNBOOK.md`
- Deployment guide: `docs/DEPLOYMENT.md`
- Control permissions matrix: `docs/CONTROL_PLANE_PERMISSIONS.md`
- Operational validation template: `docs/OPERATIONAL_VALIDATION.md`
