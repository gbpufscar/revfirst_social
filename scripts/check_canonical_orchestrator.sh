#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

OPERATIONAL_FILES=(
  "README.md"
  "docs/RUNBOOK.md"
  "docs/DEPLOYMENT.md"
  "deploy/systemd/revfirst_social.service"
)

LEGACY_PATTERN='(python|python3)[[:space:]]+-m[[:space:]]+orchestrator\.manager|(python|python3)[[:space:]]+-m[[:space:]]+pipelines\.|ExecStart=.*/-m[[:space:]]+orchestrator\.manager'

if rg -n -e "$LEGACY_PATTERN" "${OPERATIONAL_FILES[@]}"; then
  echo "ERROR: Legacy orchestrator entrypoint detected in operational files."
  echo "Use only: python -m src.orchestrator.manager"
  exit 1
fi

if ! rg -n --fixed-strings "ExecStart=/usr/bin/python3 -m src.orchestrator.manager" \
  deploy/systemd/revfirst_social.service >/dev/null; then
  echo "ERROR: deploy/systemd/revfirst_social.service must use canonical src.orchestrator.manager."
  exit 1
fi

if [[ -d "pipelines" || -d "orchestrator" ]]; then
  echo "ERROR: Legacy directories 'pipelines/' and 'orchestrator/' must not exist."
  exit 1
fi

if rg -n -g "*.py" -e '(^|[[:space:]])(from|import)[[:space:]]+(pipelines|orchestrator)(\.|[[:space:]]|$)' src tests; then
  echo "ERROR: Legacy Python imports from pipelines/orchestrator detected."
  exit 1
fi

echo "OK: Canonical orchestrator entrypoint guard passed."
