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

    It also adds "Open with Astrometrics" for .fits and .fit files, for the current user only (no
    administrator rights). It does not make Astrometrics the default program for them: Windows
    leaves that choice to the user ("Open with" > "Always").
#>

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Icon = Join-Path $Root "assets\orbit.ico"
$Launcher = Join-Path $PSScriptRoot "run_astrometrics.ps1"
# Must match app.setAppUserModelId in electron/platforms/windows.js.
$AppId = "astrometrics"
# Must match TOAST_ACTIVATOR_CLSID in electron/platforms/windows.js.
$ToastActivatorId = "2CEFF8B0-F01F-4064-B293-17520A983AE2"
if (-not (Test-Path $Icon)) { throw "Icon not found: $Icon" }

# Writes the System.AppUserModel.ID and System.AppUserModel.ToastActivatorCLSID properties of a
# shortcut. WScript.Shell cannot set them, so this calls the Windows property store directly.
# Electron shows a notification through the Start menu shortcut that carries both the app ID and
# its notification activator ID; when no shortcut carries the activator ID, Electron makes its own,
# named "Electron", and the Start menu then lists the app as Electron.
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

    public static void Set(string shortcutPath, string appId, string toastActivatorId)
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
        // System.AppUserModel.ToastActivatorCLSID; 72 = VT_CLSID, a pointer to a GUID.
        PropertyKey activatorKey = new PropertyKey {
            FormatId = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), PropertyId = 26 };
        byte[] activatorBytes = new Guid(toastActivatorId).ToByteArray();
        PropVariant activator = new PropVariant { ValueType = 72, Pointer = Marshal.AllocCoTaskMem(16) };
        Marshal.Copy(activatorBytes, 0, activator.Pointer, 16);
        try
        {
            Marshal.ThrowExceptionForHR(store.SetValue(ref key, ref value));
            Marshal.ThrowExceptionForHR(store.SetValue(ref activatorKey, ref activator));
            Marshal.ThrowExceptionForHR(store.Commit());
        }
        finally
        {
            Marshal.FreeCoTaskMem(value.Pointer);
            Marshal.FreeCoTaskMem(activator.Pointer);
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
# Windows names a new pin "Astrometrics (2).lnk" when the name is taken, so the pin is found by
# the app ID it carries, not by its file name. No new pin is made: pinning is the user's choice.
$PinFolder = Join-Path $env:APPDATA "Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar"
$ShellApp = New-Object -ComObject Shell.Application
Get-ChildItem $PinFolder -Filter *.lnk -ErrorAction SilentlyContinue | ForEach-Object {
    $pinId = $ShellApp.Namespace($_.DirectoryName).ParseName($_.Name).ExtendedProperty("System.AppUserModel.ID")
    if ($pinId -eq $AppId) { $Targets += $_.FullName }
}

foreach ($path in $Targets) {
    $lnk = $Shell.CreateShortcut($path)
    $lnk.TargetPath = (Get-Command powershell.exe).Source
    $lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`""
    $lnk.WorkingDirectory = $Root
    $lnk.IconLocation = "$Icon,0"
    $lnk.Description = "Astrometrics astronomy image viewer and observatory control"
    $lnk.WindowStyle = 7
    $lnk.Save()
    [ShortcutAppId]::Set($path, $AppId, $ToastActivatorId)
    Write-Host "Created $path"
}
# Electron shows notifications through a Start menu shortcut of its own, named after electron.exe
# ("Electron.lnk"), and makes one whenever that file is missing, even when the Astrometrics
# shortcut above already carries the same IDs. A visible one makes the Start menu list the app
# as "Electron". So this script makes that file itself, hidden, with the same IDs: Electron then
# finds it and leaves it alone, and the Start menu shows only "Astrometrics".
$ElectronShortcut = Join-Path ([Environment]::GetFolderPath("Programs")) "Electron.lnk"
if (Test-Path $ElectronShortcut) { Remove-Item $ElectronShortcut -Force }
$electronLnk = $Shell.CreateShortcut($ElectronShortcut)
$electronLnk.TargetPath = Join-Path $Root "node_modules\electron\dist\electron.exe"
$electronLnk.WorkingDirectory = Join-Path $Root "node_modules\electron\dist"
$electronLnk.Save()
[ShortcutAppId]::Set($ElectronShortcut, $AppId, $ToastActivatorId)
$hidden = Get-Item $ElectronShortcut -Force
$hidden.Attributes = $hidden.Attributes -bor [IO.FileAttributes]::Hidden

# "Open with Astrometrics" for FITS files. A program ID describes how to open them (the launcher
# with -File); each extension lists it under OpenWithProgids, which adds it to the "Open with"
# menu without taking over the default program.
$ProgId = "Astrometrics.FITS"
$Classes = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey("Software\Classes")
$PowerShell = (Get-Command powershell.exe).Source
$progKey = $Classes.CreateSubKey($ProgId)
$progKey.SetValue("", "FITS Image")
$progKey.SetValue("FriendlyTypeName", "FITS Image")
$progKey.CreateSubKey("DefaultIcon").SetValue("", "`"$Icon`",0")
$progKey.CreateSubKey("shell\open").SetValue("FriendlyAppName", "Astrometrics")
$progKey.CreateSubKey("shell\open\command").SetValue(
    "", "`"$PowerShell`" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`" -File `"%1`"")
foreach ($extension in @(".fits", ".fit")) {
    $Classes.CreateSubKey("$extension\OpenWithProgids").SetValue($ProgId, "", [Microsoft.Win32.RegistryValueKind]::String)
}
# Tells Explorer that file associations changed, so the menu shows the entry at once.
Add-Type -Namespace Astrometrics -Name Shell -MemberDefinition @"
[DllImport("shell32.dll")] public static extern void SHChangeNotify(int eventId, uint flags, IntPtr item1, IntPtr item2);
"@
[Astrometrics.Shell]::SHChangeNotify(0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)
Write-Host "Added Open with Astrometrics for .fits and .fit files."

Write-Host "Done. Double-click Astrometrics on the Desktop to start it."
Write-Host "If the taskbar still shows two icons, unpin Astrometrics and pin it again from the Start menu."
