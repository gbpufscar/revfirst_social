# RevFirst_Social - Master Implementation Plan

Version: 1.5  
Status: Active (Living Document)  
Last Updated: 2026-02-17  
Canonical Authority: `/docs/PROJECT_CANONICAL.md`

---

## 1. Official Product Decision

- RevFirst_Social is Multi-Tenant SaaS from day 1.
- Initial operation uses one workspace (`revfirst`) with architecture ready for scale.
- No structural refactor should be required to open paid onboarding.

---

## 2. Correct Execution Order (Official)

1. Foundation SaaS
2. Multi-Tenant Core
3. Billing + Plan Enforcement
4. Usage Tracking
5. Ingestion Layer (Read-only)
6. Domain Agents
7. Publishing Engine
8. Scheduler + Locks
9. Telegram Seed + Daily Post
10. Hardening + Observability

Strategic sequence summary:

`Foundation -> Isolation -> Billing -> Usage -> Ingestion -> Agents -> Publish -> Locks -> Intelligence -> Hardening`

---

## 3. Hard Architecture Boundaries

Mandatory separation:
- Domain never depends on Stripe.
- Agents never depend on HTTP framework details.
- Publisher never depends on Billing logic.
- Billing never depends on Agents.

---

## 4. Non-Negotiable Cross-Cutting Requirements

1. Authorization model:
- Create `workspace_users` plus roles (`owner`, `admin`, `member`).
- `users` alone is not sufficient for multi-tenant authorization.

2. Tenant isolation by design:
- Every domain table must include mandatory `workspace_id`.
- Add composite indexes (`workspace_id`, `created_at`) in domain tables.
- In PostgreSQL, apply RLS policies for tenant isolation.

3. Secret handling:
- Never store `api_keys` plaintext.
- Store API keys and sensitive tokens as hash-only where applicable.
- Support rotation and revocation.
- Never log tokens or secrets.

4. Stripe webhook idempotency:
- Webhook processing must enforce unique `event_id`.
- Duplicate events must be safely ignored after first successful processing.

5. Plan-limit performance:
- Maintain `usage_logs` plus daily aggregated usage per workspace.
- `check_plan_limit(workspace_id)` must use aggregated reads, not brute-force counts.

6. Multi-tenant scheduler safety:
- Orchestrator must acquire lock per workspace before pipeline execution.
- No mixed namespace execution and no duplicate concurrent run for same workspace.

7. Migration discipline:
- Alembic is mandatory from day 1.
- No manual production schema drift.

---

## 5. Phase Roadmap

### Phase 1 - Foundation SaaS
Objective: system boots, isolates baseline infra, and is versionable.

Scope:
- Modular project structure.
- Docker running.
- FastAPI app up.
- `GET /health` active.
- PostgreSQL + Redis connectivity.
- Connection pool and env-based config.
- Alembic configured with migration pipeline.

Done when:
- `docker compose up` works.
- `alembic upgrade head` works.
- `/health` returns `200`.
- Structured logs are active.

### Phase 2 - Multi-Tenant Core
Objective: implement tenant isolation and authorization core.

Scope:
- Create tables:
  - `users`
  - `workspaces`
  - `workspace_users`
  - `roles`
  - `api_keys`
- Mandatory `workspace_id` in domain entities.
- Composite indexes (`workspace_id`, `created_at`).
- PostgreSQL RLS enabled and tested.
- Auth and permission stack:
  - JWT
  - middleware
  - role enforcement
  - protected endpoints

Done when:
- User can access only its own workspace data.
- RLS blocks cross-workspace access.
- Roles (`owner/admin/member`) are enforced.

### Phase 3 - Billing Core
Objective: enforce behavior limits through billing model, even before monetization.

Scope:
- Stripe client with sandbox tests.
- Webhook idempotency base:
  - `stripe_events` table
  - unique `event_id`
  - dedup working
- Plan enforcement contract:
  - `plans.yaml`
  - `check_plan_limit()`
  - early integration point with publisher path

Done when:
- Subscription event changes plan/status.
- Plan changes effective limits.
- Duplicate webhook does not create side effects.

### Phase 4 - Usage Tracking
Objective: plan enforcement with scalable reads.

Scope:
- `usage_logs` for action-level events.
- `workspace_daily_usage` aggregation.
- `check_plan_limit(workspace_id)` reads aggregation.

Done when:
- Limits are enforced correctly.
- Queries are fast.
- Usage logs are auditable.

### Phase 5 - Ingestion Layer (Read-only)
Objective: start touching X safely without publish risk.

Scope:
- Workspace-scoped OAuth for X.
- Secure token handling.
- Open-call ingestion pipeline.
- Candidate storage with workspace namespace.
- Intent classification and scoring with isolated logs.

Done when:
- Candidates are persisted per workspace.
- No publish action occurs.
- Flow is easy to debug.

### Phase 6 - Domain Agents
Objective: build pure agent logic with strict contracts.

Scope:
- Reply Writer.
- Brand Consistency.
- Anti-Cringe Guard.
- Thread Detector.
- Lead Tracker.
- All agents return validated JSON payloads.
- No direct publish calls.

Done when:
- Replies are generated.
- Guards block invalid output.
- Agent suite is fully unit tested in isolation.

### Phase 7 - Publishing Engine
Objective: controlled publication through a single gateway.

Scope:
- One Publisher component is the only X write path.
- Plan check before publish.
- Cooldown per thread/author.
- Audit logging.

Done when:
- Publish works.
- Plan and cooldown limits are respected.
- Audit trail is complete.

### Phase 8 - Scheduler + Locks
Objective: safe multi-tenant orchestration at scale.

Scope:
- Redis lock per workspace with TTL.
- Multi-tenant loop:
  - iterate active workspaces
  - acquire lock
  - run workspace pipeline
- Strict namespace isolation.

Done when:
- No race condition under concurrent runs.
- No data mixing between workspaces.
- Execution remains isolated.

### Phase 9 - Telegram Seed + Daily Post
Objective: integrate human seed intelligence.

Scope:
- Telegram webhook.
- Style extractor.
- Daily Post engine using seed memory.
- Guard validation before queue/publish.

Done when:
- Seed is stored.
- Daily post uses seed context.
- Output passes brand and cringe guards.

### Phase 10 - Hardening + Observability
Objective: production stability before operational beta.

Scope:
- Sentry.
- `GET /metrics`.
- Rate limit by IP.
- Basic load testing.
- Stability checks.

Done when:
- 7 days stable run without critical failure.
- No tenant data leakage.
- No unresolved critical incidents.

---

## 6. Do Not Build Now

- Do not build complex dashboard yet.
- Do not build heavy frontend yet.
- Do not open public onboarding yet.
- Do not prematurely optimize architecture.
- Do not split into microservices now.

---

## 7. SaaS MVP / Beta Operational Gate

Operational beta is ready only when:
- Multi-tenant core is active and isolated.
- 1 workspace runs end-to-end safely.
- Billing and plan enforcement are active.
- Ingestion and publish operate with controls.
- Usage and audit logs are reliable.
- Scheduler runs with per-workspace locks.
- Observability baseline is active.

---

## 8. Progress Tracking (Update at Every Phase Transition)

Status legend: `NOT_STARTED`, `IN_PROGRESS`, `DONE`, `BLOCKED`

| Phase | Name | Status | Started On | Completed On | Notes |
|---|---|---|---|---|---|
| 1 | Foundation SaaS | DONE | 2026-02-17 | 2026-02-17 | Scaffold done. Local validation passed (compose, DB/Redis, Alembic). GitHub governance active (protected main + required check + PR flow). Coolify deploy accepted by product decision after internal health/version + migration checks. |
| 2 | Multi-Tenant Core | DONE | 2026-02-17 | 2026-02-17 | Added Alembic migration `20260217_0002` with `users`, `workspaces`, `workspace_users`, `roles`, `api_keys` (+ `workspace_events`), seeded roles, composite indexes, and PostgreSQL RLS policies. Delivered JWT auth + middleware + role-based dependencies and endpoints `POST /auth/login`, `POST /workspaces`, `GET /workspaces/{id}`. Added hash-based security helpers for passwords/API keys. Validation: `pytest` (18 passed) and `ruff` passed locally. |
| 3 | Billing Core | DONE | 2026-02-17 | 2026-02-17 | Added Alembic migration `20260217_0003` with `subscriptions`, `stripe_events` (idempotent `event_id`), `usage_logs`, and `workspace_daily_usage`. Implemented `config/plans.yaml`, plan loading and `check_plan_limit()` on daily aggregates, plus `record_usage()`. Added Stripe webhook endpoint `POST /billing/webhook` with signature verification and idempotent processing. Validation: `pytest` (22 passed), `ruff` passed, Alembic upgrade/downgrade chain verified (`0001 -> 0003 -> base`). |
| 4 | Usage Tracking | DONE | 2026-02-17 | 2026-02-17 | Implemented app-layer usage service (`consume_workspace_action`, `get_workspace_daily_usage`) on top of `usage_logs` + `workspace_daily_usage` aggregate model. Enforced transactional check->record flow with `PlanLimitExceededError` guard, ensuring limits use daily aggregation and not brute-force counts. Validation: `pytest` (25 passed), `ruff` passed, compile check passed. |
| 5 | Ingestion Layer (Read-only) | DONE | 2026-02-17 | 2026-02-17 | Added Alembic migration `20260217_0004` with `x_oauth_tokens` (workspace-scoped, hash + encrypted token fields) and `ingestion_candidates` (workspace namespace + intent/score indexing), both with PostgreSQL RLS policies. Implemented X OAuth endpoints (`/integrations/x/oauth/*`) and read-only ingestion endpoints (`POST /ingestion/open-calls/run`, `GET /ingestion/candidates/{workspace_id}`) with workspace-scoped auth. Added open-call intent classification and opportunity scoring pipeline without any publish path. Validation: `pytest` and `ruff` passed, migration chain validated through `0004`. |
| 6 | Domain Agents | NOT_STARTED | - | - | - |
| 7 | Publishing Engine | NOT_STARTED | - | - | - |
| 8 | Scheduler + Locks | NOT_STARTED | - | - | - |
| 9 | Telegram Seed + Daily Post | NOT_STARTED | - | - | - |
| 10 | Hardening + Observability | NOT_STARTED | - | - | - |

Update protocol:
- At phase start: set `IN_PROGRESS` and fill `Started On`.
- At phase completion: set `DONE`, fill `Completed On`, record objective evidence in `Notes`.
- If blocked: set `BLOCKED` and describe unblock condition in `Notes`.

---

## 9. Change Log

- 2026-02-17: Created living implementation plan with SaaS-ready controls.
- 2026-02-17: Reordered official roadmap to Foundation-first sequence (Phase 1-10) and added completion gates, anti-scope list, and phase update protocol.
- 2026-02-17: Phase 1 marked as IN_PROGRESS with initial foundation scaffold delivered.
- 2026-02-17: Phase 1 scaffold validated locally with Docker, DB/Redis health, and Alembic baseline migration; external GitHub/Coolify checks pending.
- 2026-02-17: GitHub governance completed for Phase 1 (repo published, branch protection enforced, required check `lint-test-build`, PR-based merge flow verified). Coolify manual verification remains.
- 2026-02-17: Phase 1 marked as DONE after infrastructure, governance, migrations, and internal runtime validation were completed and accepted.
- 2026-02-17: Phase 2 implementation completed with multi-tenant schema + RLS, workspace membership/role model, JWT auth and role enforcement, and protected workspace endpoints.
- 2026-02-17: Phase 2 validation completed with local automated checks (`pytest` and `ruff`).
- 2026-02-17: Phase 3 implemented with Stripe webhook idempotency (`stripe_events.event_id` unique), plans contract in `config/plans.yaml`, and plan-limit enforcement API (`check_plan_limit` + daily usage aggregation).
- 2026-02-17: Phase 3 validated by test suite, lint, and Alembic migration chain checks.
- 2026-02-17: Phase 4 implemented with application usage service consuming plan limits and writing both raw (`usage_logs`) and aggregated (`workspace_daily_usage`) usage records.
- 2026-02-17: Phase 4 validated with dedicated tests for consume, limit-exceeded behavior, and daily aggregated reads.
- 2026-02-17: Phase 5 implemented with workspace-scoped X OAuth token storage (hash + encrypted) and read-only open-call ingestion pipeline with intent + opportunity scoring.
- 2026-02-17: Phase 5 validated with integration tests for OAuth/token storage and candidate ingestion, plus migration contract and lint/test checks.
