<#
.SYNOPSIS
    Creates the Astrometrics Desktop and Start menu shortcuts on Windows.

.DESCRIPTION
    Windows counterpart of build/linux/install_desktop_icon.sh. Makes an "Astrometrics" shortcut
    (icon: assets\orbit.ico) on the Desktop and in the Start menu. It starts
    build\windows\run_astrometrics.ps1 with no console window. Pin it to the taskbar afterwards
    by right-clicking it. Safe to run again; it replaces the old shortcuts.
#>

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Icon = Join-Path $Root "assets\orbit.ico"
$Launcher = Join-Path $PSScriptRoot "run_astrometrics.ps1"
if (-not (Test-Path $Icon)) { throw "Icon not found: $Icon" }

$Shell = New-Object -ComObject WScript.Shell
$Targets = @(
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "Astrometrics.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Programs")) "Astrometrics.lnk")
)
foreach ($path in $Targets) {
    $lnk = $Shell.CreateShortcut($path)
    $lnk.TargetPath = (Get-Command powershell.exe).Source
    $lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`""
    $lnk.WorkingDirectory = $Root
    $lnk.IconLocation = "$Icon,0"
    $lnk.Description = "Astrometrics astronomy image viewer and observatory control"
    $lnk.WindowStyle = 7
    $lnk.Save()
    Write-Host "Created $path"
}
Write-Host "Done. Double-click Astrometrics on the Desktop to start it."
