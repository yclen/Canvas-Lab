<#
make_shortcut.ps1 — create a Start Menu shortcut for Canvas App, ready to pin.

The shortcut runs game_loop.py with pythonw (no console window), uses a local copy of icon.ico,
and carries the AppUserModelID "Yaakov.CanvasApp" — the same ID game_loop.py sets
at startup — so the pinned icon and the running window share one taskbar button.

Usage:   powershell -ExecutionPolicy Bypass -File make_shortcut.ps1 [-Pythonw <path>]
Then:    Start menu > find "Canvas App" > right-click > Pin to taskbar.
Re-run it if you move the folder or change Python installs.
#>
param(
    [string]$Pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
)

$ErrorActionPreference = "Stop"
$AppId = "Yaakov.CanvasApp"   # keep in sync with game_loop.py
$AppDir = $PSScriptRoot
$Script = Join-Path $AppDir "game_loop.py"
$Lnk = Join-Path ([Environment]::GetFolderPath("Programs")) "Canvas App.lnk"
$PinnedLnk = Join-Path $env:APPDATA "Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\Canvas App.lnk"

if (-not $Pythonw -or -not (Test-Path $Pythonw)) {
    throw "pythonw.exe not found; pass -Pythonw <path to the python that has pygame>"
}

# The app folder lives on Google Drive, which mounts after Explorer draws the
# taskbar at login, so an icon read from there shows blank. Keep a local copy.
$IconDir = Join-Path $env:LOCALAPPDATA "CanvasApp"
New-Item -ItemType Directory -Force $IconDir | Out-Null
$Icon = Join-Path $IconDir "icon.ico"
Copy-Item (Join-Path $AppDir "icon.ico") $Icon -Force

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class ShortcutMaker {
    [ComImport, Guid("00021401-0000-0000-C000-000000000046")] class CShellLink {}

    [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown),
     Guid("000214F9-0000-0000-C000-000000000046")]
    interface IShellLinkW {
        void GetPath(IntPtr a, int b, IntPtr c, int d);
        void GetIDList(out IntPtr p);
        void SetIDList(IntPtr p);
        void GetDescription(IntPtr a, int b);
        void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string s);
        void GetWorkingDirectory(IntPtr a, int b);
        void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string s);
        void GetArguments(IntPtr a, int b);
        void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string s);
        void GetHotkey(out short h);
        void SetHotkey(short h);
        void GetShowCmd(out int c);
        void SetShowCmd(int c);
        void GetIconLocation(IntPtr a, int b, out int c);
        void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string s, int i);
        void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string s, int r);
        void Resolve(IntPtr hwnd, int flags);
        void SetPath([MarshalAs(UnmanagedType.LPWStr)] string s);
    }

    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    struct PropertyKey { public Guid fmtid; public uint pid; }

    [StructLayout(LayoutKind.Explicit, Size = 24)]
    struct PropVariant {
        [FieldOffset(0)] public ushort vt;
        [FieldOffset(8)] public IntPtr ptr;
    }

    [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown),
     Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")]
    interface IPropertyStore {
        void GetCount(out uint c);
        void GetAt(uint i, out PropertyKey k);
        void GetValue(ref PropertyKey k, out PropVariant v);
        void SetValue(ref PropertyKey k, ref PropVariant v);
        void Commit();
    }

    [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown),
     Guid("0000010b-0000-0000-C000-000000000046")]
    interface IPersistFile {
        void GetClassID(out Guid g);
        [PreserveSig] int IsDirty();
        void Load([MarshalAs(UnmanagedType.LPWStr)] string f, int mode);
        void Save([MarshalAs(UnmanagedType.LPWStr)] string f, bool remember);
        void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string f);
        void GetCurFile(out IntPtr f);
    }

    public static void Create(string lnk, string target, string args, string workDir,
                              string icon, string appId) {
        var link = (IShellLinkW)new CShellLink();
        link.SetPath(target);
        link.SetArguments(args);
        link.SetWorkingDirectory(workDir);
        link.SetIconLocation(icon, 0);
        link.SetDescription("Canvas App");

        // PKEY_AppUserModel_ID
        var key = new PropertyKey { fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid = 5 };
        var val = new PropVariant { vt = 31 /* VT_LPWSTR */, ptr = Marshal.StringToCoTaskMemUni(appId) };
        try {
            var store = (IPropertyStore)link;
            store.SetValue(ref key, ref val);
            store.Commit();
        } finally {
            Marshal.FreeCoTaskMem(val.ptr);
        }
        ((IPersistFile)link).Save(lnk, true);
    }
}
"@

[ShortcutMaker]::Create($Lnk, $Pythonw, "`"$Script`"", $AppDir, $Icon, $AppId)
Write-Host "Created $Lnk"
Write-Host "  runs: $Pythonw `"$Script`""
# Pinning copies the .lnk, so refresh an existing pin too.
if (Test-Path $PinnedLnk) {
    [ShortcutMaker]::Create($PinnedLnk, $Pythonw, "`"$Script`"", $AppDir, $Icon, $AppId)
    Write-Host "Updated taskbar pin."
} else {
    Write-Host "Now: Start menu > 'Canvas App' > right-click > Pin to taskbar."
}
