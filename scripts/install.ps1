<#
.SYNOPSIS
    Installe le Référentiel mappings OMOP dans un environnement isolé (sans accès Internet).

.DESCRIPTION
    1. vérifie Python 3.12 (les wheels du dossier wheels\ sont compilés pour CPython 3.12, Windows 64 bits) ;
    2. crée l'environnement virtuel .venv ;
    3. installe les dépendances UNIQUEMENT depuis wheels\ (pip --no-index) ;
    4. crée .env depuis .env.example s'il n'existe pas ;
    5. applique les migrations SQL (sauf -SkipMigrate).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Python "C:\Python312\python.exe" -SkipMigrate
#>
[CmdletBinding()]
param(
    [string]$Python = "",
    [string]$VenvDir = "",
    [switch]$SkipMigrate
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $VenvDir) { $VenvDir = Join-Path $Root ".venv" }
$Wheels = Join-Path $Root "wheels"
$Requirements = Join-Path $Root "requirements.txt"

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Stop-WithError([string]$Message) { Write-Host "ERREUR : $Message" -ForegroundColor Red; exit 1 }

function Find-Python {
    if ($Python) { return $Python }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.12 -c "import sys" 2>$null
        if ($LASTEXITCODE -eq 0) { return "py" }
    }
    $candidate = Get-Command python -ErrorAction SilentlyContinue
    if ($candidate) { return $candidate.Source }
    Stop-WithError "Python 3.12 introuvable. Préciser son chemin : -Python C:\chemin\python.exe"
}

function Invoke-Python([string]$Command, [string[]]$Arguments) {
    if ($Command -eq "py") { & py -3.12 @Arguments } else { & $Command @Arguments }
}

if (-not (Test-Path $Wheels)) { Stop-WithError "Dossier des wheels introuvable : $Wheels" }

$PythonCmd = Find-Python
$version = Invoke-Python $PythonCmd @("-c", "import sys; print('%d.%d' % sys.version_info[:2])")
if ($version -ne "3.12") {
    Stop-WithError "Python $version détecté ; les wheels fournis exigent Python 3.12 (option -Python pour en choisir un autre)."
}

if (-not (Test-Path (Join-Path $VenvDir "Scripts\python.exe"))) {
    Write-Step "Création de l'environnement virtuel $VenvDir"
    Invoke-Python $PythonCmd @("-m", "venv", $VenvDir)
    if ($LASTEXITCODE -ne 0) { Stop-WithError "création de l'environnement virtuel impossible" }
}
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

Write-Step "Installation des dépendances depuis $Wheels (hors ligne)"
& $VenvPython -m pip install --no-index --find-links $Wheels -r $Requirements --disable-pip-version-check -q
if ($LASTEXITCODE -ne 0) { Stop-WithError "installation des dépendances impossible (voir les messages de pip)" }

$EnvFile = Join-Path $Root ".env"
if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $Root ".env.example") $EnvFile
    Write-Host "Fichier .env créé depuis .env.example : vérifier DATABASE_URL avant de démarrer." -ForegroundColor Yellow
}

if (-not $SkipMigrate) {
    Write-Step "Application des migrations SQL"
    & $VenvPython (Join-Path $Root "scripts\migrate.py")
    if ($LASTEXITCODE -ne 0) {
        Stop-WithError "migrations impossibles : vérifier DATABASE_URL dans .env et que PostgreSQL est démarré"
    }
}

Write-Host "Installation terminée. Lancer l'application : scripts\start.ps1 (ou demarrer.cmd)" -ForegroundColor Green
