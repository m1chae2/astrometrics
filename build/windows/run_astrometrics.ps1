<#
.SYNOPSIS
    Starts Astrometrics on native Windows: backend, Vite dev server, then Electron.

.DESCRIPTION
    Windows counterpart of build/linux/run_astrometrics.sh (foreground mode). The backend is
    started first and not waited for, so Electron's splash screen covers its start-up. A backend
    or Vite server that is already running is reused. When the Electron window closes, only the
    processes this run started are stopped. Logs go to `.run_logs\`.

    Usage:
        run_astrometrics.ps1 [-Mode "Planetarium"] [-Lan] [-Port 5173] [-File path.fits]
        run_astrometrics.ps1 -Stop      # stop the backend and Vite, whoever started them
#>
param(
    [string]$Mode = "",
    [switch]$Lan,
    [int]$Port = 5173,
    [string]$File = "",
    [switch]$Stop
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root
$LogDir = Join-Path $Root ".run_logs"
New-Item -ItemType Directory -Force $LogDir | Out-Null
$BackendUrl = "http://127.0.0.1:5000/"
$ViteUrl = "http://127.0.0.1:$Port"

# Answers $true when an HTTP server responds at the URL (any status code counts).
function Test-Url([string]$Url) {
    try {
        Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        return $null -ne $_.Exception.Response
    }
}

# Kills a process and everything it started.
function Stop-Tree([int]$ProcessId) {
    & taskkill /PID $ProcessId /T /F 2>&1 | Out-Null
}

# Kills whatever is listening on a TCP port.
function Stop-Port([int]$LocalPort) {
    Get-NetTCPConnection -State Listen -LocalPort $LocalPort -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Tree $_.OwningProcess }
}

# Waits until a URL answers; returns $false on timeout or when $Process exits first.
function Wait-Url([string]$Url, [int]$Seconds, $Process = $null) {
    for ($i = 0; $i -lt $Seconds * 2; $i++) {
        if (Test-Url $Url) { return $true }
        if ($Process -and $Process.HasExited) { return $false }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

if ($Stop) {
    Stop-Port 5000; Stop-Port $Port; Stop-Port 9222
    Write-Host "Astrometrics services stopped."
    return
}

# Check that setup_windows.ps1 has been run.
$Python = Join-Path $Root ".venv-win\Scripts\python.exe"
$Electron = Join-Path $Root "node_modules\electron\dist\electron.exe"
$Vite = Join-Path $Root "node_modules\vite\bin\vite.js"
if (-not ((Test-Path $Python) -and (Test-Path $Electron) -and (Test-Path $Vite))) {
    Write-Host "Not set up yet. Run build\windows\setup_windows.ps1 first." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

$started = @()
$backend = $null
try {
    # Backend (Electron's splash waits for /api/ready, so this does not wait for the catalog).
    if (Test-Url $BackendUrl) {
        Write-Host "Backend already running; reusing it."
    } else {
        $env:PYTHONPATH = $Root
        $env:PYTHONFAULTHANDLER = "1"
        $env:ASTROMETRICS_BIND_HOST = if ($Lan) { "0.0.0.0" } else { "127.0.0.1" }
        $backend = Start-Process $Python -ArgumentList "-u", "-m", "backend.main_backend" `
            -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $LogDir "backend.log") `
            -RedirectStandardError (Join-Path $LogDir "backend.err.log")
        $started += $backend
    }

    # Vite dev server.
    if (Test-Url $ViteUrl) {
        Write-Host "Vite already running; reusing it."
    } else {
        $viteArgs = @($Vite, "--port", $Port)
        if ($Lan) { $viteArgs += @("--host", "0.0.0.0") }
        $vite = Start-Process "node" -ArgumentList $viteArgs -WorkingDirectory $Root `
            -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $LogDir "vite.log") `
            -RedirectStandardError (Join-Path $LogDir "vite.err.log")
        $started += $vite
        if (-not (Wait-Url $ViteUrl 30 $vite)) { throw "Vite did not start; see .run_logs\vite.err.log" }
    }

    # The backend only has to be listening; the splash covers the catalog load.
    if (-not (Wait-Url $BackendUrl 60 $backend)) { throw "Backend did not start; see .run_logs\backend.err.log" }

    # Electron, waited on so the services stop when its window closes.
    $rendererUrl = $ViteUrl
    if ($Mode) { $rendererUrl += "/?mode=" + [uri]::EscapeDataString($Mode) }
    $env:ELECTRON_RENDERER_URL = $rendererUrl
    $env:SKIP_BACKEND = "1"
    $env:NODE_ENV = "development"
    $electronArgs = @(".", "--remote-debugging-port=9222")
    if ($File) { $electronArgs += '"' + (Resolve-Path $File).Path + '"' }
    $electronProc = Start-Process $Electron -ArgumentList $electronArgs -WorkingDirectory $Root `
        -PassThru -RedirectStandardOutput (Join-Path $LogDir "electron.log") `
        -RedirectStandardError (Join-Path $LogDir "electron.err.log")
    $electronProc.WaitForExit()
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Read-Host "Press Enter to close"
} finally {
    foreach ($p in $started) { if ($p -and -not $p.HasExited) { Stop-Tree $p.Id } }
}
