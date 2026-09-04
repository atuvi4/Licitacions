#!/usr/bin/env bash
# Mode de desenvolupament SENSE Docker: SQLite + storage de fitxers local.
# Nomes per a desenvolupament. El cami oficial es docker compose (PostgreSQL + MinIO).
set -euo pipefail
cd "$(dirname "$0")/../apps/api"

export DATABASE_URL="sqlite+aiosqlite:///./var/dev.db"
export STORAGE_BACKEND="local"
export STORAGE_LOCAL_PATH="./var/storage"
export SECRET_KEY="dev-local-only-secret-do-not-use-in-production"
export ENVIRONMENT="local"
export COOKIE_SECURE="false"

mkdir -p var

if [ ! -f var/dev.db ]; then
  python -m app.dev_bootstrap
  python -m app.seed
fi

exec python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
