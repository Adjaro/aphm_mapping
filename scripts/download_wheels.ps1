<#
.SYNOPSIS
    (Poste connecté) Télécharge dans wheels\ les wheels de requirements.txt pour l'environnement isolé.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\download_wheels.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\download_wheels.ps1 -Platform manylinux2014_x86_64
#>
[CmdletBinding()]
param(
    [string]$Platform = "win_amd64",
    [string]$PythonVersion = "3.12",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Target = Join-Path $Root "wheels"
New-Item -ItemType Directory -Force $Target | Out-Null
& $Python -m pip download -r (Join-Path $Root "requirements.txt") -d $Target --only-binary=:all: `
    --platform $Platform --python-version $PythonVersion --implementation cp
if ($LASTEXITCODE -ne 0) { Write-Host "ERREUR : téléchargement impossible" -ForegroundColor Red; exit 1 }
Write-Host "Wheels disponibles dans $Target" -ForegroundColor Green
