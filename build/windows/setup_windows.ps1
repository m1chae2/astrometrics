<#
.SYNOPSIS
    Sets up Astrometrics for native Windows: a Python virtual environment and the Node packages.

.DESCRIPTION
    Creates `.venv-win` (not `.venv`, which the Ubuntu/WSL side of this checkout may already use),
    installs the project into it, seeds the local config file, sets up Siril and GraXpert
    (the stacking and gradient-removal programs, each downloaded from its own project and checked
    against a pinned checksum), downloads Siril's Gaia astrometry catalog (about 1.5 GB, checked the
    same way), runs `npm install`, and builds the UI MCP server in ui\mcp.
    At the end it warns when Windows long paths are turned off, and says how to turn them on.
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

# 5. Siril (stacking) and GraXpert (gradient removal): each downloaded from its own project,
# checked against its pinned checksum, unpacked into the data folder and set in the settings.
# A program already named in the settings is kept.
Write-Host "Setting up Siril and GraXpert..." -ForegroundColor Yellow
$env:PYTHONPATH = $ProjectRoot
& $VenvPython -m astrometricslib.scripts.set_up_programs
if ($LASTEXITCODE -ne 0) { throw "Setting up Siril or GraXpert failed." }

# 5b. Siril's Gaia astrometry catalog (about 1.5 GB), which its plate solver reads: downloaded,
# decompressed and checked against a pinned size and checksum. Already installed and matching: kept.
Write-Host "Setting up Siril's Gaia astrometry catalog (about 1.5 GB)..." -ForegroundColor Yellow
& $VenvPython -m astrometricslib.scripts.set_up_siril_catalog
if ($LASTEXITCODE -ne 0) { throw "Setting up Siril's Gaia astrometry catalog failed." }

# 6. Node packages.
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

# 7. The UI MCP server (ui/mcp) has its own packages and must be compiled; .mcp.json runs
# ui\mcp\dist\index.js. Without this, Claude Code shows the "astrometrics-ui" server as failed.
Write-Host "Building the UI MCP server..." -ForegroundColor Yellow
Push-Location (Join-Path $ProjectRoot "ui\mcp")
try {
    npm ci --ignore-scripts --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw "npm ci in ui\mcp failed." }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "Building the UI MCP server failed." }
} finally {
    Pop-Location
}

# 8. Long paths. Windows limits a path to 260 characters unless long paths are turned on, and a
# library on Google Drive (G:\My Drive\Library\lights\<target>\<telescope>\<camera>\...) plus
# Siril's file names can pass that. Turning them on needs administrator rights, so this only
# warns and says how; it changes nothing.
$LongPaths = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name LongPathsEnabled -ErrorAction SilentlyContinue).LongPathsEnabled
if ($LongPaths -ne 1) {
    Write-Host "Long paths are turned off on this computer. Paths longer than 260 characters," -ForegroundColor Yellow
    Write-Host "for example deep folders in a Google Drive library, can then fail to open." -ForegroundColor Yellow
    Write-Host "To turn them on, run this once in PowerShell as administrator, then restart:" -ForegroundColor Yellow
    Write-Host '    New-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force' -ForegroundColor Yellow
}

Write-Host "Setup complete. Next: build\windows\install_desktop_icon.ps1" -ForegroundColor Green
