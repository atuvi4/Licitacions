# Mode de desenvolupament SENSE Docker: SQLite + storage de fitxers local.
# Nomes per a desenvolupament. El cami oficial es docker compose (PostgreSQL + MinIO).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\apps\api")

$env:DATABASE_URL      = "sqlite+aiosqlite:///./var/dev.db"
$env:STORAGE_BACKEND   = "local"
$env:STORAGE_LOCAL_PATH= "./var/storage"
$env:SECRET_KEY        = "dev-local-only-secret-do-not-use-in-production"
$env:ENVIRONMENT       = "local"
$env:COOKIE_SECURE     = "false"

New-Item -ItemType Directory -Force -Path "var" | Out-Null

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

if (-not (Test-Path "var\dev.db")) {
    & $python -m app.dev_bootstrap
    & $python -m app.seed
}

& $python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
