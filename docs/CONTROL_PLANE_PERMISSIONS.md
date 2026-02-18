# Control Plane Permissions Matrix

Status: Active (Phase 12)  
Last Updated: 2026-02-18

## Roles

- `owner`
- `admin`
- `member`

## Command Permissions (V1)

| Command | owner | admin | member | Notes |
|---|---|---|---|---|
| `/help` | yes | yes | yes | Read-only help text |
| `/status` | yes | yes | yes | Runtime and lock status |
| `/metrics` | yes | yes | yes | Daily summary and plan usage |
| `/daily_report` | yes | yes | yes | Alias for `/metrics` |
| `/queue` | yes | yes | yes | Read-only queue preview |
| `/approve <id>` | yes | yes | no | Mutating, idempotent |
| `/reject <id>` | yes | yes | no | Mutating |
| `/pause` | yes | yes | no | Workspace execution control |
| `/resume` | yes | yes | no | Workspace execution control |
| `/pause global` | yes | no | no | Global kill switch enable |
| `/resume global` | yes | no | no | Global kill switch disable |
| `/run <pipeline> [dry_run=true]` | yes | yes | no | Manual pipeline trigger |
| `/channel enable\|disable ...` | yes | yes | no | Channel feature flags |
| `/limit replies\|posts <n>` | yes | yes | no | Temporary override with TTL |
| `/seed <text>` | yes | yes | yes | Seed ingestion + interpretation |

## Security Rules

1. Telegram user must be present in `allowed_telegram_ids` from `config/telegram_admins.yaml`.
2. Telegram user must be bound to a valid `user_id` in `config/telegram_admins.yaml`.
3. Bound `user_id` must belong to the workspace through `workspace_users` + `roles`.
4. Command role must satisfy this matrix.
5. Every command writes `admin_actions` with status/result/error/duration/request correlation.
6. Mutating operations enforce idempotency where required (`/approve`, `/run`).
