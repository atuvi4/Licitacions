# Arrencada completa de l'entorn local a Windows.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "S'ha creat .env a partir de .env.example."
}

docker compose up -d --build

Write-Host "Esperant que l'API estigui llesta..."
do {
    Start-Sleep -Seconds 2
    try { $ok = (Invoke-WebRequest -Uri "http://localhost:8000/health" -UseBasicParsing).StatusCode -eq 200 }
    catch { $ok = $false }
} until ($ok)

docker compose exec -T api python -m app.seed

Write-Host ""
Write-Host "Entorn llest:"
Write-Host "  Frontend : http://localhost:3000"
Write-Host "  API      : http://localhost:8000/docs"
Write-Host "  MinIO    : http://localhost:9001"
Write-Host ""
Write-Host "  Usuari   : demo@licitacions.dev"
Write-Host "  Clau     : Demo12345!"
