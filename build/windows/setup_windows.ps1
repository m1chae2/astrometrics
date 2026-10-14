<#
.SYNOPSIS
    Sets up Astrometrics for native Windows: a Python virtual environment and the Node packages.

.DESCRIPTION
    Creates `.venv-win` (not `.venv`, which the Ubuntu/WSL side of this checkout may already use),
    installs the project into it, seeds the local config file, and runs `npm install`.
    Astrometrics needs Python 3.14 or newer. Run it from anywhere:

        powershell -ExecutionPolicy Bypass -File build\windows\setup_windows.ps1
#>

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $ProjectRoot
$VenvDir = Join-Path $ProjectRoot ".venv-win"

Write-Host "Setting up Astrometrics for Windows in $ProjectRoot" -ForegroundColor Cyan

# 1. Find Python 3.14+ through the `py` launcher.
$PyVersion = $null
foreach ($version in @("3.14", "3.15")) {
    & py "-$version" -c "import sys" 2>$null
    if ($LASTEXITCODE -eq 0) { $PyVersion = $version; break }
}
if (-not $PyVersion) {
    Write-Host "Python 3.14+ was not found. Install it with:" -ForegroundColor Red
    Write-Host "    winget install Python.Python.3.14" -ForegroundColor Yellow
    exit 1
}

# 2. Create the virtual environment.
if (-not (Test-Path (Join-Path $VenvDir "Scripts\python.exe"))) {
    Write-Host "Creating $VenvDir ..." -ForegroundColor Yellow
    & py "-$PyVersion" -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "Could not create the virtual environment." }
}
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

# 3. Install the project (editable) with the MCP extra.
Write-Host "Installing Python packages (this takes a few minutes)..." -ForegroundColor Yellow
& $VenvPython -m pip install --upgrade pip hatchling editables setuptools_scm Cython numpy
& $VenvPython -m pip install --no-build-isolation -e ".[mcp]"
if ($LASTEXITCODE -ne 0) { throw "pip install failed." }

# 4. Seed the machine-specific config from the tracked template.
$Config = Join-Path $ProjectRoot "astrometricslib\astrometrics.config.toml"
$Template = Join-Path $ProjectRoot "astrometricslib\astrometrics.config.example.toml"
if (-not (Test-Path $Config) -and (Test-Path $Template)) {
    Copy-Item $Template $Config
    Write-Host "Created $Config - edit it to set frames_path." -ForegroundColor Yellow
}

# 5. Node packages.
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Host "Node.js / npm not found. Install it with: winget install OpenJS.NodeJS.LTS" -ForegroundColor Red
    exit 1
}
Write-Host "Installing Node packages..." -ForegroundColor Yellow
# --ignore-scripts: the project's "prepare" script is POSIX shell (pre-commit hook install), which
# cmd.exe cannot run. Electron's own download step is run by hand below instead.
npm install --ignore-scripts
if ($LASTEXITCODE -ne 0) { throw "npm install failed." }
if (-not (Test-Path "node_modules\electron\dist\electron.exe")) {
    node node_modules\electron\install.js
    if ($LASTEXITCODE -ne 0) { throw "Electron download failed." }
}

Write-Host "Setup complete. Next: build\windows\install_desktop_icon.ps1" -ForegroundColor Green
