<#
.SYNOPSIS
    Creates the Astrometrics Desktop and Start menu shortcuts on Windows.

.DESCRIPTION
    Windows counterpart of build/linux/install_desktop_icon.sh. Makes an "Astrometrics" shortcut
    (icon: assets\orbit.ico) on the Desktop and in the Start menu. It starts
    build\windows\run_astrometrics.ps1 with no console window. Pin it to the taskbar afterwards
    by right-clicking it. Safe to run again; it replaces the old shortcuts.

    Each shortcut carries the app's taskbar ID (AppUserModelID), the same one the app window sets
    in electron/platforms/windows.js. Windows uses that ID to match a pinned icon to the running
    window. Without it the shortcut counts as PowerShell, so the open app shows as a second icon
    next to the pinned one. A taskbar pin made before this fix is updated too.
#>

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Icon = Join-Path $Root "assets\orbit.ico"
$Launcher = Join-Path $PSScriptRoot "run_astrometrics.ps1"
# Must match app.setAppUserModelId in electron/platforms/windows.js.
$AppId = "astrometrics"
if (-not (Test-Path $Icon)) { throw "Icon not found: $Icon" }

# Writes the System.AppUserModel.ID property of a shortcut. WScript.Shell cannot set it, so this
# calls the Windows property store directly.
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class ShortcutAppId
{
    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    public struct PropertyKey { public Guid FormatId; public uint PropertyId; }

    [StructLayout(LayoutKind.Explicit)]
    public struct PropVariant
    {
        [FieldOffset(0)] public ushort ValueType;
        [FieldOffset(8)] public IntPtr Pointer;
    }

    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPropertyStore
    {
        int GetCount(out uint count);
        int GetAt(uint index, out PropertyKey key);
        int GetValue(ref PropertyKey key, out PropVariant value);
        int SetValue(ref PropertyKey key, ref PropVariant value);
        int Commit();
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    static extern void SHGetPropertyStoreFromParsingName(
        string path, IntPtr bindContext, int flags, ref Guid interfaceId, out IPropertyStore store);

    public static void Set(string shortcutPath, string appId)
    {
        Guid interfaceId = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99");
        IPropertyStore store;
        // 2 = GPS_READWRITE.
        SHGetPropertyStoreFromParsingName(shortcutPath, IntPtr.Zero, 2, ref interfaceId, out store);
        // System.AppUserModel.ID.
        PropertyKey key = new PropertyKey {
            FormatId = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), PropertyId = 5 };
        // 31 = VT_LPWSTR, a text value.
        PropVariant value = new PropVariant { ValueType = 31, Pointer = Marshal.StringToCoTaskMemUni(appId) };
        try
        {
            Marshal.ThrowExceptionForHR(store.SetValue(ref key, ref value));
            Marshal.ThrowExceptionForHR(store.Commit());
        }
        finally
        {
            Marshal.FreeCoTaskMem(value.Pointer);
            Marshal.ReleaseComObject(store);
        }
    }
}
"@

$Shell = New-Object -ComObject WScript.Shell
$Targets = @(
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "Astrometrics.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Programs")) "Astrometrics.lnk")
)
# A taskbar pin is its own copy of the shortcut; update it if the user already pinned one.
$Pinned = Join-Path $env:APPDATA "Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\Astrometrics.lnk"
if (Test-Path $Pinned) { $Targets += $Pinned }

foreach ($path in $Targets) {
    $lnk = $Shell.CreateShortcut($path)
    $lnk.TargetPath = (Get-Command powershell.exe).Source
    $lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`""
    $lnk.WorkingDirectory = $Root
    $lnk.IconLocation = "$Icon,0"
    $lnk.Description = "Astrometrics astronomy image viewer and observatory control"
    $lnk.WindowStyle = 7
    $lnk.Save()
    [ShortcutAppId]::Set($path, $AppId)
    Write-Host "Created $path"
}
Write-Host "Done. Double-click Astrometrics on the Desktop to start it."
Write-Host "If the taskbar still shows two icons, unpin Astrometrics and pin it again from the Start menu."
