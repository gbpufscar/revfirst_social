# RevFirst_Social - Master Implementation Plan

Version: 2.0  
Status: Active (Living Document)  
Last Updated: 2026-02-18  
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
11. Single-Workspace Excellence
12. Control Plane via Telegram (Priority)
13. ContentObject + Channel Abstraction
14. Email Module (Real Publisher)
15. Blog Module (Real Publisher)
16. Instagram Module (Real Publisher)
17. Controlled Beta (2-5 workspaces)
18. Enterprise Hardening

Strategic sequence summary:

`Foundation -> Isolation -> Billing -> Usage -> Ingestion -> Agents -> Publish -> Locks -> Intelligence -> Hardening -> Excellence -> Control -> Abstraction -> Channels -> Beta -> Enterprise`

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

### Phase 11 - Single-Workspace Excellence (30-day window)
Objective: perfect operation in one real workspace before scaling.

Scope:
- Add `config/runtime.yaml`:
  - `primary_workspace_id`
  - `single_workspace_mode`
- Scheduler behavior:
  - if `single_workspace_mode=true`, run only `primary_workspace_id`
  - if `false`, iterate active workspaces
- Add business counters:
  - `revfirst_replies_generated_total{workspace_id}`
  - `revfirst_replies_published_total{workspace_id}`
  - `revfirst_reply_blocked_total{workspace_id,reason}`
  - `revfirst_daily_post_published_total{workspace_id}`
  - `revfirst_seed_used_total{workspace_id}`
  - `revfirst_publish_errors_total{workspace_id,channel}`
- Propagate `workspace_id` into Sentry context.
- Create operational validation document (`docs/OPERATIONAL_VALIDATION.md`).

Mandatory improvements in this phase:
- Define a canonical queue contract (`QueueItem`) before Control Plane commands.
- Define config precedence:
  - `ENV` > temporary override > `runtime.yaml` > defaults.

Done when:
- Scheduler isolates execution to one workspace when configured.
- New counters are visible on `/metrics`.
- 30-day validation template exists and is actively used.

### Phase 12 - Control Plane via Telegram (Priority)
Objective: run the system from Telegram as command center with strict governance.

Scope:
- Create control module:
  - `src/control/telegram_bot.py`
  - `src/control/command_router.py`
  - `src/control/command_schema.py`
  - `src/control/security.py`
  - `src/control/formatters.py`
  - `src/control/handlers/*`
- Security:
  - whitelist Telegram IDs
  - enforce workspace role permissions
  - map command to allowed roles
- Create `admin_actions` table with full command audit.
- Implement commands:
  - `/help`, `/status`, `/metrics`
  - `/queue`, `/approve`, `/reject`
  - `/pause`, `/resume`
  - `/run <pipeline>`
  - `/channel enable|disable`
  - `/limit` override
  - `/seed`

Mandatory improvements in this phase:
- Add `global_kill_switch` in addition to workspace pause.
- Enforce idempotency:
  - `/approve <id>` cannot publish twice
  - `/run <pipeline>` cannot duplicate active run
- Add `dry_run=true` support to `/run`.
- Extend `admin_actions` payload with:
  - `status`
  - `result_summary`
  - `error`
  - `duration_ms`
  - `request_id`
  - `idempotency_key`
- Create permission matrix doc: `docs/CONTROL_PLANE_PERMISSIONS.md`.

Done when:
- Unauthorized users always receive `unauthorized`.
- Every admin command emits audit trail.
- Pause and kill switch effectively stop execution.
- Control commands are covered by tests and pass CI.

### Phase 13 - ContentObject + Channel Abstraction
Objective: prepare multichannel without duplicating intelligence.

Scope:
- Create canonical `ContentObject` model in domain.
- Update Reply Writer and Daily Post to return `ContentObject`.
- Add channel adapters:
  - `src/channels/base.py`
  - `src/channels/x/*`
  - `src/channels/email/*` (stub)
  - `src/channels/blog/*` (stub)
  - `src/channels/instagram/*` (stub)
- Add channel router with feature-flag and limit awareness.

Done when:
- X publish path still works unchanged.
- Email/blog/instagram generate preview payloads without publishing.
- Router respects feature flags and pause controls.

### Phase 14 - Email Module (Real)
Objective: enable real email publishing from existing abstraction.

Scope:
- Integrate one provider (Resend, SES, or SendGrid).
- Add formatter + publisher implementation.
- Add Telegram command compatibility for email channel controls.

Done when:
- Email channel can publish with audit + limits + approval flow.

### Phase 15 - Blog Module (Real)
Objective: enable blog publishing and repurpose engine.

Scope:
- Integrate CMS/webhook publish path.
- Add content repurpose flow from thread/seed to long-form.

Done when:
- Blog publish works with queue/approval/audit and rollback-safe behavior.

### Phase 16 - Instagram Module (Real)
Objective: enable Instagram publishing and basic calendar controls.

Scope:
- Integrate Meta Graph API.
- Add formatter for caption/hashtags.
- Add scheduling hooks in orchestration.

Done when:
- Instagram publish flow is stable under approval and limit checks.

### Phase 17 - Controlled Beta (2-5 workspaces)
Objective: expand safely with feature flags and observability.

Scope:
- Onboard 2-5 workspaces with staged rollout.
- Enable channel features progressively by flag.
- Track tenant isolation and operational quality metrics.

Done when:
- No cross-workspace leakage.
- No unresolved critical incidents during beta window.

### Phase 18 - Enterprise Hardening
Objective: finalize enterprise-grade controls after product behavior is stable.

Scope:
- Advanced backups and restore drills.
- Audit trail expansion and retention policies.
- Configuration and strategy versioning.
- Compliance-grade operational controls.

Done when:
- Recovery and audit procedures are formally verified.
- System meets enterprise reliability and governance requirements.

---

## 6. Do Not Build Now

- Do not build complex dashboard yet.
- Do not build heavy frontend yet.
- Do not open public onboarding yet.
- Do not prematurely optimize architecture.
- Do not split into microservices now.
- Do not enable real multi-channel publishing before Phase 13 baseline is stable.

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
| 6 | Domain Agents | DONE | 2026-02-17 | 2026-02-17 | Added pure domain agents under `src/domain/agents` (Reply Writer, Brand Consistency, Anti-Cringe Guard, Thread Detector, Lead Tracker) with strict Pydantic JSON contracts and composition pipeline, without HTTP/Stripe/publish dependencies. Validation: dedicated unit tests for each agent + pipeline, `pytest` (37 passed) and `ruff` passed. |
| 7 | Publishing Engine | DONE | 2026-02-17 | 2026-02-17 | Added publishing engine as single X write path via `/publishing` routes and `src/publishing/service.py`. Implemented plan check before publish, thread/author cooldown enforcement, and full audit trail (`publish_audit_logs` + `publish_cooldowns`) with PostgreSQL RLS through Alembic `20260217_0005`. Validation: unit/integration tests for publish success, cooldown block, and plan-limit block; `pytest` and `ruff` passed; migration chain validated through `0005`. |
| 8 | Scheduler + Locks | DONE | 2026-02-18 | 2026-02-18 | Added orchestrator stack under `src/orchestrator` with Redis workspace lock manager (`SET NX EX` + safe token release), multi-tenant scheduler loop, per-workspace DB context isolation, and scheduler audit events in `workspace_events`. Added CLI runner `python -m src.orchestrator.manager` and test coverage for lock-skip, failure recovery, and execution isolation. |
| 9 | Telegram Seed + Daily Post | DONE | 2026-02-18 | 2026-02-18 | Added Telegram integration (`/integrations/telegram`) with webhook secret validation, seed persistence, style extraction memory, and workspace-scoped listing. Added Daily Post engine (`/daily-post`) with guard validation (Brand Consistency + Anti-Cringe) and optional auto-publish via existing publishing engine. Added Alembic migration `20260218_0006` for `telegram_seeds` and `daily_post_drafts` with PostgreSQL RLS. |
| 10 | Hardening + Observability | DONE | 2026-02-18 | 2026-02-18 | Added Sentry bootstrap (`src/core/observability.py`), Prometheus-style `GET /metrics`, production IP rate limit middleware with limit headers and block metrics, plus basic load test script (`scripts/loadtest_basic.py`) and Makefile target. Validation: new tests for metrics/rate-limit/observability, full `pytest` and `ruff` green. Production checks completed on `social.revfirst.cloud` (`/health`, `/version`, `/metrics` all `200`) and Sentry smoke event received in `production`. |
| 11 | Single-Workspace Excellence | IN_PROGRESS | 2026-02-18 | - | Technical baseline delivered: runtime single-workspace mode (`config/runtime.yaml` + scheduler behavior), business counters exposed in `/metrics`, and Sentry workspace context propagation in API/scheduler. Pending completion criterion: sustained 30-day operational validation window using `docs/OPERATIONAL_VALIDATION.md`. |
| 12 | Control Plane via Telegram | DONE | 2026-02-18 | 2026-02-18 | Implemented control module (`src/control/*`) with Telegram command router, whitelist+workspace-role enforcement, audited `admin_actions`, command suite (`/help`, `/status`, `/metrics`, `/daily_report`, `/queue`, `/approve`, `/reject`, `/pause`, `/resume`, `/run`, `/channel`, `/limit`, `/seed`), idempotent `/approve` and `/run`, `dry_run` pipeline execution, global kill switch, and Redis-backed pause/run locks. Added migration `20260218_0007` (`admin_actions`, `approval_queue_items`, `workspace_control_settings`, `pipeline_runs`) + RLS and test coverage for security/router/pause/queue-approve. |
| 13 | ContentObject + Channel Abstraction | DONE | 2026-02-19 | 2026-02-19 | Added canonical `ContentObject` usage in Reply Writer and Daily Post flows. Introduced channel adapter layer (`src/channels/*`) and workspace-safe channel routing (`src/domain/routing/channel_router.py`) with feature flags, pause controls, and optional plan-limit awareness before auto-publish. Validation: `ruff` and `pytest` passed (64 tests), including new phase-13 tests for reply-to-content conversion, routing controls, and daily-post channel preview outputs. |
| 14 | Email Module (Real) | DONE | 2026-02-19 | 2026-02-19 | Added real email provider integration (`src/integrations/email/resend_client.py`) and upgraded email publisher from stub to live provider-backed send path with recipients/from-address controls. Added `publish_email` service with plan limits, usage aggregation (`publish_email`), audit log (`publish_audit_logs` platform `email`), and workspace event emission. Integrated approval flow (`/approve`) and manual pipeline execution (`/run daily_post`, `/run execute_approved`) for `email` queue items. Validation: `ruff` and `pytest` passed (77 tests), including new tests for email publish service, billing limits, control-plane email approval, and daily-post email queueing. |
| 15 | Blog Module (Real) | DONE | 2026-02-19 | 2026-02-19 | Added real blog publishing via webhook provider integration (`src/integrations/blog/webhook_client.py`) and upgraded `BlogPublisher` from stub to live publish path. Added `publish_blog` service with plan-limit enforcement, usage aggregation (`publish_blog`), audit logging (`publish_audit_logs` platform `blog`), and workspace event emission. Integrated control-plane approval and execution flows for `blog` queue items and extended daily-post queue expansion when blog channel is enabled. Validation: `ruff` and `pytest` passed (83 tests), including new tests for blog publish service, billing limits, and control-plane blog queue/publish behavior. |
| 16 | Instagram Module (Real) | NOT_STARTED | - | - | Planned: Meta Graph integration with formatter + schedule controls. |
| 17 | Controlled Beta (2-5 workspaces) | NOT_STARTED | - | - | Planned: staged rollout by feature flags with strict isolation validation. |
| 18 | Enterprise Hardening | NOT_STARTED | - | - | Planned: advanced backups, versioning, and enterprise governance controls after behavior maturity. |

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
- 2026-02-17: Phase 6 implemented with pure domain agents and explicit JSON contracts under `src/domain/agents` (reply, brand, cringe, thread, lead).
- 2026-02-17: Phase 6 validated with isolated agent tests and end-to-end domain pipeline contract checks.
- 2026-02-17: Phase 7 implemented with a single publishing engine path to X, plan checks before publish, cooldown guards, and persistent publish audit logs.
- 2026-02-17: Phase 7 validated with API-level publish tests (success, cooldown block, plan-limit block) and migration contract checks.
- 2026-02-18: Phase 8 implemented with workspace-level Redis locks and multi-tenant scheduler orchestration (`src/orchestrator/*`).
- 2026-02-18: Phase 8 validated with scheduler lock/isolation tests and integrated CLI run path for operations.
- 2026-02-18: Phase 9 implemented with Telegram seed ingestion, style memory extraction, and guarded daily post generation with optional publish path.
- 2026-02-18: Phase 9 validated with API integration tests (webhook auth, seed persistence, guarded generation, and auto-publish usage accounting) plus migration contract checks.
- 2026-02-18: Phase 10 implemented with Sentry bootstrap, `/metrics` endpoint, production IP rate limiting middleware, and basic load-test script/target.
- 2026-02-18: Phase 10 validated with dedicated tests for observability stack and successful lint/test suite.
- 2026-02-18: Production routing standardized on `social.revfirst.cloud` to avoid collision with official `www.revfirst.cloud` surface.
- 2026-02-18: Coolify routing labels corrected for host-based domain rule and HTTPS cert resolver; production endpoint validation succeeded.
- 2026-02-18: Upgraded master plan to v1.8 with post-Phase-10 roadmap (Phases 11-18) and control-plane hardening requirements (queue contract, idempotency, permission matrix, kill switch, and config precedence).
- 2026-02-18: Phase 11 started with technical execution baseline complete (single-workspace runtime mode, business counters, Sentry workspace context, and operational validation template). 30-day validation window remains open for final completion.
- 2026-02-18: Phase 12 implemented with control-plane module, command router/handlers, Telegram authorization model (whitelist + workspace role), admin command audit trail, and manual pipeline execution with lock + idempotency safeguards.
- 2026-02-18: Phase 12 validated with new control test suite (`tests/control/*`), migration contract test (`tests/test_phase12_migration_contract.py`), full `ruff` pass, and full `pytest` pass.
- 2026-02-19: Phase 13 implemented with canonical `ContentObject` integration in reply and daily-post generation flows and channel adapters for X/email/blog/instagram.
- 2026-02-19: Added workspace-safe channel router with Redis-driven channel flags, pause/kill-switch checks, and optional plan-limit gating for X auto-publish.
- 2026-02-19: Phase 13 validated by automated checks (`.venv/bin/ruff check src tests`, `.venv/bin/pytest -q`) with 64 tests passing.
- 2026-02-19: Phase 14 implemented with provider-backed email publishing (`ResendClient`), updated email channel publisher, and shared publishing guardrails (`publish_email` with plan-limit enforcement, usage tracking, and audit trail).
- 2026-02-19: Control plane integration extended for email queue lifecycle (`item_type=email`) via `/approve` and `/run` pipeline execution, including daily-post queue expansion when email channel is enabled.
- 2026-02-19: Phase 14 validated by automated checks (`.venv/bin/ruff check src tests`, `.venv/bin/pytest -q`) with 77 tests passing.
- 2026-02-19: Phase 15 implemented with webhook-based blog integration (`BlogWebhookClient`) and live `BlogPublisher`, replacing preview-only blog stub behavior.
- 2026-02-19: Added `publish_blog` service and control-plane support for `blog` queue lifecycle (`/approve` and `/run execute_approved`) plus daily-post queue expansion when blog channel is enabled.
- 2026-02-19: Phase 15 validated by automated checks (`.venv/bin/ruff check src tests`, `.venv/bin/pytest -q`) with 83 tests passing.
