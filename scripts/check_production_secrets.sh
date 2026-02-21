#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="${1:-.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: env file not found: $ENV_FILE"
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

required=(
  "SECRET_KEY"
  "TOKEN_ENCRYPTION_KEY"
  "DATABASE_URL"
  "REDIS_URL"
  "X_CLIENT_ID"
  "X_CLIENT_SECRET"
  "X_REDIRECT_URI"
  "TELEGRAM_WEBHOOK_SECRET"
  "TELEGRAM_ADMINS_FILE_PATH"
  "APP_PUBLIC_BASE_URL"
  "PUBLISHING_DIRECT_API_INTERNAL_KEY"
)

missing=()
for key in "${required[@]}"; do
  value="${!key:-}"
  if [[ -z "${value// }" ]]; then
    missing+=("$key")
  fi
done

if [[ "${ENV:-}" != "production" ]]; then
  echo "ERROR: ENV must be 'production' in $ENV_FILE."
  exit 1
fi

if [[ "${PUBLISHING_DIRECT_API_ENABLED:-false}" != "false" ]]; then
  echo "ERROR: PUBLISHING_DIRECT_API_ENABLED must be false in production."
  exit 1
fi

if [[ "${#missing[@]}" -gt 0 ]]; then
  echo "ERROR: Missing required production secrets/config:"
  printf ' - %s\n' "${missing[@]}"
  exit 1
fi

echo "OK: production secrets/config are complete in $ENV_FILE."
