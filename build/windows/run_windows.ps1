<#
.SYNOPSIS
    Starts Astrometrics from built web pages, with Electron starting the backend.

.DESCRIPTION
    Builds the web pages and runs `npm start`, so Electron starts the backend
    itself (electron/platforms/windows.js picks `.venv-win\Scripts\python.exe`).
    This is how the installed app starts, so use it to check that path. For
    day-to-day work use run_astrometrics.ps1, which runs the Vite dev server
    and starts the backend separately.

    Usage:
        build\windows\run_windows.ps1
#>

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $ProjectRoot

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Starting Astrometrics..." -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

if (-not (Test-Path ".venv-win\Scripts\python.exe")) {
    Write-Host "Not set up yet. Run build\windows\setup_windows.ps1 first." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path "node_modules")) {
    Write-Host "node_modules not found. Run build\windows\setup_windows.ps1 first." -ForegroundColor Red
    exit 1
}

# Electron writes its own log lines to the console as well.
$env:ELECTRON_ENABLE_LOGGING = "true"

Write-Host "Building the web pages..." -ForegroundColor Cyan
npm run build
if ($LASTEXITCODE -ne 0) {
    Write-Host "Web page build failed." -ForegroundColor Red
    exit 1
}

Write-Host "Starting Electron..." -ForegroundColor Green
npm start
