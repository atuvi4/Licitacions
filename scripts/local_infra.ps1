<#
.SYNOPSIS
    Aixeca PostgreSQL i MinIO reals SENSE Docker i sense permisos d'administrador.

.DESCRIPTION
    El camí oficial del projecte és `docker compose up`. Aquest script existeix
    només per a màquines on no es pot instal·lar Docker Desktop (cal admin), i
    aixeca exactament els mateixos components amb els mateixos ports i
    credencials que declara docker-compose.yml:

        PostgreSQL 16  -> localhost:5433
        MinIO (S3)     -> localhost:9000  (consola: 9001)

    Els binaris i les dades viuen fora del repositori, a
    $env:USERPROFILE\.local\licitacions-infra (o LICITACIONS_INFRA_HOME).

.PARAMETER Action
    init    Descarrega els binaris (si cal), crea el clúster i el bucket.
    start   Arrenca PostgreSQL i MinIO.
    stop    Atura els dos serveis.
    status  Mostra si responen.

.EXAMPLE
    .\scripts\local_infra.ps1 init
    .\scripts\local_infra.ps1 start
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet("init", "start", "stop", "status")]
    [string]$Action = "status"
)

$ErrorActionPreference = "Stop"

$Home_ = if ($env:LICITACIONS_INFRA_HOME) { $env:LICITACIONS_INFRA_HOME }
         else { Join-Path $env:USERPROFILE ".local\licitacions-infra" }

$PgBin     = Join-Path $Home_ "pgsql\bin"
$PgData    = Join-Path $Home_ "pgdata"
$MinioBin  = Join-Path $Home_ "bin\minio.exe"
$McBin     = Join-Path $Home_ "bin\mc.exe"
$MinioData = Join-Path $Home_ "miniodata"
$Logs      = Join-Path $Home_ "logs"

# Han de coincidir amb .env.example / docker-compose.yml
$PgPort     = 5433
$PgUser     = "licitacions"
$PgPassword = "licitacions"
$PgDatabase = "licitacions"
$MinioUser  = "minioadmin"
$MinioPass  = "minioadmin"
$Bucket     = "licitacions-documents"

$PG_VERSION = "16.9-1"

function Info($message) { Write-Host "  $message" }

function Invoke-Init {
    New-Item -ItemType Directory -Force -Path $Home_, $Logs, (Join-Path $Home_ "bin"), (Join-Path $Home_ "dl") | Out-Null

    if (-not (Test-Path $PgBin)) {
        Info "Descarregant PostgreSQL $PG_VERSION (binaris, sense instal·lador)..."
        $zip = Join-Path $Home_ "dl\pg.zip"
        Invoke-WebRequest -UseBasicParsing -OutFile $zip `
            "https://get.enterprisedb.com/postgresql/postgresql-$PG_VERSION-windows-x64-binaries.zip"
        Expand-Archive -Path $zip -DestinationPath $Home_ -Force
    }
    if (-not (Test-Path $MinioBin)) {
        Info "Descarregant MinIO..."
        Invoke-WebRequest -UseBasicParsing -OutFile $MinioBin "https://dl.min.io/server/minio/release/windows-amd64/minio.exe"
        Invoke-WebRequest -UseBasicParsing -OutFile $McBin "https://dl.min.io/client/mc/release/windows-amd64/mc.exe"
    }

    if (-not (Test-Path (Join-Path $PgData "PG_VERSION"))) {
        Info "Creant el clúster de PostgreSQL..."
        $pwfile = Join-Path $Home_ "pgpass.tmp"
        Set-Content -Path $pwfile -Value $PgPassword -NoNewline -Encoding ascii
        & (Join-Path $PgBin "initdb.exe") -D $PgData -U $PgUser --auth=scram-sha-256 --pwfile=$pwfile -E UTF8 | Out-Null
        Remove-Item $pwfile -Force
    }

    Invoke-Start

    $env:PGPASSWORD = $PgPassword
    $exists = & (Join-Path $PgBin "psql.exe") -h localhost -p $PgPort -U $PgUser -d postgres -tAc `
        "SELECT 1 FROM pg_database WHERE datname='$PgDatabase'"
    if (-not $exists) {
        Info "Creant la base de dades '$PgDatabase'..."
        & (Join-Path $PgBin "createdb.exe") -h localhost -p $PgPort -U $PgUser $PgDatabase
    }

    Info "Creant el bucket '$Bucket'..."
    & $McBin alias set licitacions-local "http://localhost:9000" $MinioUser $MinioPass | Out-Null
    & $McBin mb --ignore-existing "licitacions-local/$Bucket" | Out-Null

    Write-Host "`nInfraestructura llesta." -ForegroundColor Green
    Invoke-Status
}

function Invoke-Start {
    if (-not (Test-Path (Join-Path $PgData "PG_VERSION"))) {
        throw "El clúster no existeix. Executa primer: .\scripts\local_infra.ps1 init"
    }
    New-Item -ItemType Directory -Force -Path $Logs, $MinioData | Out-Null

    & (Join-Path $PgBin "pg_ctl.exe") -D $PgData status *> $null
    if ($LASTEXITCODE -ne 0) {
        Info "Arrencant PostgreSQL al port $PgPort..."
        # Ni crida directa amb canonada ni `-Wait`: a Windows, `pg_ctl start` es
        # queda viu mentre corre el servidor, aixi que esperar-lo bloquejaria
        # l'script per sempre. S'engega despenjat i es fa sondeig fins que el
        # servidor respon.
        Start-Process -FilePath (Join-Path $PgBin "pg_ctl.exe") `
            -ArgumentList "-D", "`"$PgData`"", "-o", "`"-p $PgPort`"", "-l", "`"$(Join-Path $Logs 'postgres.log')`"", "start" `
            -WindowStyle Hidden
        $deadline = (Get-Date).AddSeconds(45)
        do {
            Start-Sleep -Milliseconds 500
            & (Join-Path $PgBin "pg_ctl.exe") -D $PgData status *> $null
            $ready = $LASTEXITCODE -eq 0
        } until ($ready -or (Get-Date) -gt $deadline)
        if (-not $ready) { throw "PostgreSQL no ha arrencat. Mira $Logs\postgres.log" }
    }

    if (-not (Get-NetTCPConnection -LocalPort 9000 -State Listen -ErrorAction SilentlyContinue)) {
        Info "Arrencant MinIO als ports 9000/9001..."
        $env:MINIO_ROOT_USER = $MinioUser
        $env:MINIO_ROOT_PASSWORD = $MinioPass
        Start-Process -FilePath $MinioBin `
            -ArgumentList "server", $MinioData, "--address", ":9000", "--console-address", ":9001" `
            -RedirectStandardOutput (Join-Path $Logs "minio.out.log") `
            -RedirectStandardError  (Join-Path $Logs "minio.err.log") `
            -WindowStyle Hidden
        $deadline = (Get-Date).AddSeconds(30)
        do {
            Start-Sleep -Milliseconds 500
            try {
                Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "http://localhost:9000/minio/health/live" | Out-Null
                $ready = $true
            } catch { $ready = $false }
        } until ($ready -or (Get-Date) -gt $deadline)
        if (-not $ready) { throw "MinIO no ha arrencat. Mira $Logs\minio.err.log" }
    }
}

function Invoke-Stop {
    if (Test-Path (Join-Path $PgData "PG_VERSION")) {
        Start-Process -FilePath (Join-Path $PgBin "pg_ctl.exe") `
            -ArgumentList "-D", "`"$PgData`"", "-m", "fast", "stop" -WindowStyle Hidden
        $deadline = (Get-Date).AddSeconds(30)
        do {
            Start-Sleep -Milliseconds 500
            & (Join-Path $PgBin "pg_ctl.exe") -D $PgData status *> $null
        } until ($LASTEXITCODE -ne 0 -or (Get-Date) -gt $deadline)
        Info "PostgreSQL aturat."
    }
    Get-Process -Name "minio" -ErrorAction SilentlyContinue | Stop-Process -Force
    Info "MinIO aturat."
}

function Invoke-Status {
    & (Join-Path $PgBin "pg_ctl.exe") -D $PgData status *> $null
    if ($LASTEXITCODE -eq 0) { Info "PostgreSQL : ACTIU  (localhost:$PgPort, bd '$PgDatabase')" }
    else { Info "PostgreSQL : aturat" }

    try {
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://localhost:9000/minio/health/live" | Out-Null
        Info "MinIO      : ACTIU  (S3 localhost:9000, consola http://localhost:9001)"
    } catch { Info "MinIO      : aturat" }
}

switch ($Action) {
    "init"   { Invoke-Init }
    "start"  { Invoke-Start; Invoke-Status }
    "stop"   { Invoke-Stop }
    "status" { Invoke-Status }
}

# `pg_ctl status` deixa un codi de sortida diferent de 0 quan el servidor esta
# aturat, i aixo faria semblar que l'script ha fallat.
exit 0
