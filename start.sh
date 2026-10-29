#!/usr/bin/env bash
# One-command start-up: venv + requirements, PostgreSQL discovery, .env,
# migrations, data import, checks, API. See scripts/start.py --help.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
for candidate in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then exec "$candidate" scripts/start.py "$@"; fi
done
echo "No python3 found on PATH" >&2; exit 1
