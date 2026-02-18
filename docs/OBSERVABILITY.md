# RevFirst_Social Observability Baseline (Phase 10)

Status: Active  
Last Updated: 2026-02-18

## Scope

Phase 10 provides the minimum observability and hardening baseline:
- Sentry initialization (optional by env)
- Prometheus-style metrics endpoint
- IP rate limit middleware (production-only)
- Basic load test script

## Runtime Controls

Environment variables:
- `SENTRY_DSN`
- `SENTRY_TRACES_SAMPLE_RATE` (0.0 to 1.0)
- `METRICS_ENABLED`
- `IP_RATE_LIMIT_ENABLED`
- `IP_RATE_LIMIT_REQUESTS_PER_WINDOW`
- `IP_RATE_LIMIT_WINDOW_SECONDS`

## Endpoints

- `GET /health`: DB + Redis health signal.
- `GET /version`: app metadata.
- `GET /metrics`: Prometheus text exposition (when enabled).

## Rate Limit Behavior

- Applied only when:
  - `ENV` is `prod` or `production`
  - `IP_RATE_LIMIT_ENABLED=true`
- Response when blocked:
  - HTTP `429`
  - headers:
    - `x-rate-limit-limit`
    - `x-rate-limit-remaining`
    - `x-rate-limit-reset`

## Metrics Emitted

- `revfirst_build_info`
- `revfirst_process_uptime_seconds`
- `revfirst_http_requests_total`
- `revfirst_http_request_duration_seconds_sum`
- `revfirst_http_request_duration_seconds_count`
- `revfirst_rate_limit_block_total`

## Basic Load Test

Run locally:

```bash
make loadtest
```

Custom target:

```bash
python3 scripts/loadtest_basic.py --url http://localhost:18000/health --requests 500 --concurrency 30
```
