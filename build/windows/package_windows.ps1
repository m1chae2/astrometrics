# PowerShell script to package Astrometrics for Windows
# Usage: .\scripts\windows\package_windows.ps1

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path "$ScriptDir\..\.."

Set-Location $ProjectRoot

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Packaging Astrometrics for Windows..." -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# Check if orbit.ico exists
if (-not (Test-Path "assets\orbit.ico")) {
    Write-Host "Warning: assets\orbit.ico not found. Attempting to generate it..." -ForegroundColor Yellow
    if (Test-Path ".venv\Scripts\python.exe") {
        & ".\.venv\Scripts\python.exe" "scripts\windows\convert_icon.py"
    }
    else {
        Write-Host "Error: Python venv not found. Cannot generate icon." -ForegroundColor Red
        exit 1
    }
}

Write-Host "Building frontend (Vite)..." -ForegroundColor Yellow
npm run build
if ($LASTEXITCODE -ne 0) {
    Write-Host "Frontend build failed." -ForegroundColor Red
    exit 1
}

Write-Host "Running Electron Forge Make..." -ForegroundColor Green
npm run electron-forge:make

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "Packaging complete!" -ForegroundColor Green
    Write-Host "Installers can be found in the 'out' directory." -ForegroundColor Green
}
else {
    Write-Host ""
    Write-Host "Packaging failed." -ForegroundColor Red
    exit 1
}
