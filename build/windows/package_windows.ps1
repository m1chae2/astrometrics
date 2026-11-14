<#
.SYNOPSIS
    Packages Astrometrics as a Windows installer.

.DESCRIPTION
    Windows counterpart of `npm run electron-forge:make`. That npm script calls
    `.venv/bin/pyinstaller`, a Linux path, so this script runs each step itself
    with the Windows virtual environment (`.venv-win`, made by setup_windows.ps1):
    the icon, the web pages (Vite), the backend program (PyInstaller), then the
    installer (Electron Forge). Installers are written to the `out` folder.

    Usage:
        build\windows\package_windows.ps1
#>

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $ProjectRoot

$Python = Join-Path $ProjectRoot ".venv-win\Scripts\python.exe"

# Stops the script with a message in red.
function Stop-WithError([string]$Message) {
    Write-Host $Message -ForegroundColor Red
    exit 1
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Packaging Astrometrics for Windows..." -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

if (-not (Test-Path $Python)) {
    Stop-WithError "Not set up yet. Run build\windows\setup_windows.ps1 first."
}

# The backend is bundled with PyInstaller, which is in the "dev" extras.
& $Python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Stop-WithError "PyInstaller is missing. Install it with: .venv-win\Scripts\python.exe -m pip install pyinstaller"
}

# The installer and the window use orbit.ico; make it from the PNG if it is missing.
if (-not (Test-Path "assets\orbit.ico")) {
    Write-Host "assets\orbit.ico not found. Making it from the PNG..." -ForegroundColor Yellow
    & $Python (Join-Path $PSScriptRoot "convert_icon.py")
    if ($LASTEXITCODE -ne 0) { Stop-WithError "Could not make assets\orbit.ico." }
}

Write-Host "Building the web pages (Vite)..." -ForegroundColor Yellow
npm run build
if ($LASTEXITCODE -ne 0) { Stop-WithError "Web page build failed." }

Write-Host "Building the backend program (PyInstaller)..." -ForegroundColor Yellow
& $Python -m PyInstaller backend\backend.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { Stop-WithError "Backend build failed." }

Write-Host "Making the installer (Electron Forge)..." -ForegroundColor Yellow
npx electron-forge make
if ($LASTEXITCODE -ne 0) { Stop-WithError "Packaging failed." }

Write-Host ""
Write-Host "Packaging complete. The installers are in the 'out' folder." -ForegroundColor Green
