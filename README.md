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

## Docs
- Canonical authority: `docs/PROJECT_CANONICAL.md`
- Execution roadmap: `docs/MASTER_IMPLEMENTATION_PLAN.md`
- Observability baseline: `docs/OBSERVABILITY.md`
