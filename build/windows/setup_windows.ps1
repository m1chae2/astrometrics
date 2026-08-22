# PowerShell script to set up Python virtual environment and dependencies on Windows
# Usage: .\scripts\windows\setup_windows.ps1

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path "$ScriptDir\..\.."

Set-Location $ProjectRoot

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Setting up Astrometrics for Windows..." -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# 1. Check Python
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Error: Python 3 not found in PATH. Please install Python 3.10+." -ForegroundColor Red
    exit 1
}

# 2. Virtual Environment Setup
if (-not (Test-Path ".venv")) {
    Write-Host "Creating Python virtual environment (.venv)..." -ForegroundColor Yellow
    python -m venv .venv
}

Write-Host "Upgrading pip and installing Python packages..." -ForegroundColor Yellow
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -e .

# 3. Node Dependencies
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Host "Error: Node.js / npm not found in PATH. Please install Node.js." -ForegroundColor Red
    exit 1
}

Write-Host "Installing Node.js dependencies..." -ForegroundColor Yellow
npm install

Write-Host "========================================" -ForegroundColor Green
Write-Host "Windows environment setup complete!" -ForegroundColor Green
Write-Host "Run .\scripts\windows\run_windows.ps1 to start the application." -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
