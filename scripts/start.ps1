<#
.SYNOPSIS
    Démarre le Référentiel mappings OMOP.

.DESCRIPTION
    - installe l'application si .venv est absent (scripts\install.ps1, hors ligne) ;
    - démarre éventuellement un PostgreSQL portable (-PgBin / -PgData, ou variables PG_BIN / PG_DATA) ;
    - applique les migrations SQL en attente (sauf -SkipMigrate) ;
    - lance le serveur web et ouvre le navigateur (sauf -NoBrowser).
    Arrêt : Ctrl+C dans cette fenêtre.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\start.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -BindHost 0.0.0.0 -Port 8080 -NoBrowser
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -PgBin C:\pgsql16\pgsql\bin -PgData C:\pgsql16\data -PgPort 5433
#>
[CmdletBinding()]
param(
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8000,
    [string]$PgBin = $env:PG_BIN,
    [string]$PgData = $env:PG_DATA,
    [int]$PgPort = 5432,
    [switch]$SkipMigrate,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Stop-WithError([string]$Message) { Write-Host "ERREUR : $Message" -ForegroundColor Red; exit 1 }

Set-Location $Root

if (-not (Test-Path $VenvPython)) {
    Write-Step "Première exécution : installation"
    & (Join-Path $PSScriptRoot "install.ps1") -SkipMigrate
    if (-not (Test-Path $VenvPython)) { exit 1 }
}

if ($PgBin -and $PgData) {
    $PgCtl = Join-Path $PgBin "pg_ctl.exe"
    if (-not (Test-Path $PgCtl)) { Stop-WithError "pg_ctl.exe introuvable dans $PgBin" }
    & $PgCtl status -D $PgData *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Step "Démarrage de PostgreSQL ($PgData, port $PgPort)"
        & $PgCtl start -D $PgData -o "-p $PgPort" -l (Join-Path $PgData "postgresql.log") -w
        if ($LASTEXITCODE -ne 0) { Stop-WithError "PostgreSQL n'a pas démarré (voir $PgData\postgresql.log)" }
    }
}

if (-not $SkipMigrate) {
    Write-Step "Vérification de la base et des migrations"
    & $VenvPython (Join-Path $Root "scripts\migrate.py")
    if ($LASTEXITCODE -ne 0) {
        Stop-WithError "base inaccessible : vérifier DATABASE_URL dans .env et que PostgreSQL est démarré"
    }
}

$Url = "http://$($BindHost -replace '^0\.0\.0\.0$', 'localhost'):$Port"
Write-Host ""
Write-Host "Référentiel mappings OMOP - AP-HM : $Url" -ForegroundColor Green
Write-Host "Arrêt : Ctrl+C" -ForegroundColor DarkGray
if (-not $NoBrowser) {
    Start-Job -ScriptBlock { param($u) Start-Sleep -Seconds 3; Start-Process $u } -ArgumentList $Url | Out-Null
}
& $VenvPython -m uvicorn app.main:app --host $BindHost --port $Port
