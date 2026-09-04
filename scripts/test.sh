#!/usr/bin/env bash
# Executa la suite del backend (SQLite, sense Docker) i la comprovacio de tipus del frontend.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== Backend =="
(cd apps/api && python -m pytest -q)

echo "== Frontend =="
(cd apps/web && npm run typecheck)
