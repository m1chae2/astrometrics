<#
.SYNOPSIS
    Starts Astrometrics on native Windows: backend, Vite dev server, then Electron.

.DESCRIPTION
    Windows counterpart of build/linux/run_astrometrics.sh (foreground mode). The backend and
    the Vite dev server are started together, and Electron starts as soon as Vite listens: it
    does not wait for the backend, so Electron's splash screen covers the backend's start-up.
    The backend's process id goes to `.run_pids\backend.pid`, which Electron reads to notice a
    backend that died while starting. A backend or Vite server that is already running is
    reused. When the Electron window closes, only the processes this run started are stopped.
    Logs go to `.run_logs\`; `launcher.log` there has the time of each start-up step. Errors
    show in a message box, because the desktop shortcut runs this script in a hidden window.

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
$BackendPort = 5000
$PidDir = Join-Path $Root ".run_pids"
$BackendPidFile = Join-Path $PidDir "backend.pid"
$ViteUrl = "http://127.0.0.1:$Port"
$LauncherLog = Join-Path $LogDir "launcher.log"

# Notes one start-up step and its time in launcher.log, to find where a slow start spends its time.
function Write-Step([string]$Step) {
    Add-Content -Path $LauncherLog -Value ("{0:HH:mm:ss.fff} {1}" -f (Get-Date), $Step) -Encoding ascii
}

# Answers $true when a program listens on the local TCP port. It asks Windows for its list of
# listening ports, which is instant. A web request to a closed port is not: Windows retries the
# refused connection for about 2 seconds before giving up.
function Test-Port([int]$LocalPort) {
    $listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
    return [bool]($listeners | Where-Object { $_.Port -eq $LocalPort })
}

# Waits until a program listens on the port; returns $false on timeout or when $Process exits first.
function Wait-Port([int]$LocalPort, [int]$Seconds, $Process = $null) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Port $LocalPort) { return $true }
        if ($Process -and $Process.HasExited) { return $false }
        Start-Sleep -Milliseconds 100
    }
    return $false
}

# Shows an error where the user can see it. The desktop shortcut runs this script hidden, so text
# written to the console (or a "Press Enter" prompt) would never be seen.
function Show-Error([string]$Message) {
    Write-Host $Message -ForegroundColor Red
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show($Message, "Astrometrics", "OK", "Error") | Out-Null
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

if ($Stop) {
    Stop-Port $BackendPort; Stop-Port $Port; Stop-Port 9222
    Remove-Item $BackendPidFile -ErrorAction SilentlyContinue
    Write-Host "Astrometrics services stopped."
    return
}

# Check that setup_windows.ps1 has been run.
$Python = Join-Path $Root ".venv-win\Scripts\python.exe"
$Electron = Join-Path $Root "node_modules\electron\dist\electron.exe"
$Vite = Join-Path $Root "node_modules\vite\bin\vite.js"
if (-not ((Test-Path $Python) -and (Test-Path $Electron) -and (Test-Path $Vite))) {
    Show-Error "Not set up yet. Run build\windows\setup_windows.ps1 first."
    exit 1
}

Set-Content -Path $LauncherLog -Value ("{0:HH:mm:ss.fff} launcher started" -f (Get-Date)) -Encoding ascii
$started = @()
$backend = $null
try {
    # Backend. Nothing waits for it here: Electron's splash waits for /api/ready.
    if (Test-Port $BackendPort) {
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
        # Electron reads this to tell a backend that died while starting from a slow one.
        New-Item -ItemType Directory -Force $PidDir | Out-Null
        Set-Content -Path $BackendPidFile -Value $backend.Id -Encoding ascii
        Write-Step "backend started (process $($backend.Id))"
    }

    # Vite dev server.
    if (Test-Port $Port) {
        Write-Host "Vite already running; reusing it."
    } else {
        $viteArgs = @($Vite, "--port", $Port)
        if ($Lan) { $viteArgs += @("--host", "0.0.0.0") }
        $vite = Start-Process "node" -ArgumentList $viteArgs -WorkingDirectory $Root `
            -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $LogDir "vite.log") `
            -RedirectStandardError (Join-Path $LogDir "vite.err.log")
        $started += $vite
        Write-Step "Vite started"
        # Electron loads its pages from Vite, so Vite must listen first (about 1.5 s).
        if (-not (Wait-Port $Port 30 $vite)) { throw "Vite did not start; see .run_logs\vite.err.log" }
        Write-Step "Vite listening"
    }

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
    Write-Step "Electron started"
    $electronProc.WaitForExit()
} catch {
    Show-Error $_.Exception.Message
} finally {
    foreach ($p in $started) { if ($p -and -not $p.HasExited) { Stop-Tree $p.Id } }
    if ($backend) { Remove-Item $BackendPidFile -ErrorAction SilentlyContinue }
}
