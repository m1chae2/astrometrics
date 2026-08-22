# PowerShell script to run Astrometrics on Windows
# Usage: .\scripts\windows\run_windows.ps1

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path "$ScriptDir\..\.."

Set-Location $ProjectRoot

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Starting Astrometrics..." -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# Check for .venv
if (-not (Test-Path ".venv")) {
    Write-Host "Error: Virtual environment not found. Please run .\scripts\windows\setup_windows.ps1 first." -ForegroundColor Red
    exit 1
}

# Check for node_modules
if (-not (Test-Path "node_modules")) {
    Write-Host "Error: node_modules not found. Please run .\scripts\windows\setup_windows.ps1 first." -ForegroundColor Red
    exit 1
}

# Set environment variables for electron
$env:ELECTRON_ENABLE_LOGGING = "true"

Write-Host "Building application..." -ForegroundColor Cyan
npm run build

Write-Host "Launching with npm start..." -ForegroundColor Green
npm start
