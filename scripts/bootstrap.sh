#!/usr/bin/env bash
# Arrencada completa de l'entorn local: .env, contenidors, migracions i seed.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  echo "S'ha creat .env a partir de .env.example. Revisa'l abans de produccio."
fi

docker compose up -d --build
echo "Esperant que l'API estigui llesta..."
until curl -sf http://localhost:8000/health > /dev/null; do sleep 2; done

docker compose exec -T api python -m app.seed

cat <<'MSG'

Entorn llest:
  Frontend : http://localhost:3000
  API      : http://localhost:8000/docs
  MinIO    : http://localhost:9001

  Usuari   : demo@licitacions.dev
  Clau     : Demo12345!
MSG
