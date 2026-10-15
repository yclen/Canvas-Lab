<#
make_shortcut.ps1 — create a Start Menu shortcut for Canvas App, ready to pin.

The shortcut runs game_loop.py with pythonw (no console window), uses a local copy of taskbar.ico,
and carries the AppUserModelID "Yaakov.CanvasApp" — the same ID game_loop.py sets
at startup — so the pinned icon and the running window share one taskbar button.

Usage:   powershell -ExecutionPolicy Bypass -File make_shortcut.ps1 [-Pythonw <path>]
Then:    Start menu > find "Canvas App" > right-click > Pin to taskbar.
Re-run it if you move the folder or change Python installs.

It's safe to re-run after every update: the taskbar pin is only rewritten when the shortcut
would actually change. The taskbar caches a pin's image and doesn't reload it when the .lnk is
rewritten (the pin goes blank), so when it does change, Explorer is restarted to redraw the
taskbar (pass -NoExplorerRestart to skip that and sign out / restart Explorer yourself).
#>
param(
    [string]$Pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source,
    [switch]$NoExplorerRestart
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
# taskbar.ico is icon.ico at every size (16-256 px); a single 128 px image shows
# blank on the taskbar. game_loop.py keeps loading icon.ico, which pygame can read.
# The copy is named by its content, so a new icon gets a new path: Explorer caches icons by
# path, and new bytes under an old path keep showing the old (or a blank) image.
New-Item -ItemType Directory -Force $IconDir | Out-Null
$IconSrc = Join-Path $AppDir "taskbar.ico"
$IconHash = (Get-FileHash $IconSrc -Algorithm SHA256).Hash.Substring(0, 8).ToLower()
$Icon = Join-Path $IconDir "taskbar-$IconHash.ico"
if (-not (Test-Path $Icon)) { Copy-Item $IconSrc $Icon }

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

# Build the shortcut once, next to the icon (same drive as the Start Menu and the pin, so the
# moves below are a single rename and Explorer never reads a half-written file).
$NewLnk = Join-Path $IconDir "Canvas App.new.lnk"
[ShortcutMaker]::Create($NewLnk, $Pythonw, "`"$Script`"", $AppDir, $Icon, $AppId)

# Compare what a shortcut does, not its bytes: a .lnk also stores timestamps of pythonw.exe
# and the folders above it (they change all the time), and Explorer re-saves a pin itself.
function Lnk-Fields($path) {
    $s = (New-Object -ComObject WScript.Shell).CreateShortcut($path)
    $item = (New-Object -ComObject Shell.Application).Namespace((Split-Path $path)).ParseName((Split-Path $path -Leaf))
    "$($s.TargetPath)|$($s.Arguments)|$($s.WorkingDirectory)|$($s.IconLocation)|$($item.ExtendedProperty('System.AppUserModel.ID'))"
}
function Same-File($a, $b) {
    (Test-Path $b) -and ((Lnk-Fields $a) -eq (Lnk-Fields $b))
}
function Install-Lnk($dest) {
    $tmp = "$NewLnk.copy"
    Copy-Item $NewLnk $tmp -Force
    Move-Item $tmp $dest -Force   # a rename onto the old file
}

if (Same-File $NewLnk $Lnk) {
    Write-Host "Start Menu shortcut already up to date."
} else {
    Install-Lnk $Lnk
    Write-Host "Created $Lnk"
}
Write-Host "  runs: $Pythonw `"$Script`""

# Pinning copies the .lnk, so refresh an existing pin too — but only if it changed.
$RestartExplorer = $false
if (-not (Test-Path $PinnedLnk)) {
    Write-Host "Now: Start menu > 'Canvas App' > right-click > Pin to taskbar."
} elseif (Same-File $NewLnk $PinnedLnk) {
    Write-Host "Taskbar pin already up to date (left alone, so its icon stays)."
} else {
    Install-Lnk $PinnedLnk
    Write-Host "Updated taskbar pin."
    $RestartExplorer = $true
}
Remove-Item $NewLnk

# Old icon copies nothing points at any more.
Get-ChildItem $IconDir -Filter *.ico | Where-Object { $_.FullName -ne $Icon } | Remove-Item

if ($RestartExplorer) {
    if ($NoExplorerRestart) {
        Write-Host "The pin's icon may show blank until Explorer restarts (or you sign out and back in)."
    } else {
        Write-Host "Restarting Explorer so the taskbar redraws the pin's icon..."
        Stop-Process -Name explorer -Force
        Start-Sleep -Seconds 2
        # Windows normally relaunches the shell by itself; start it if it didn't.
        if (-not (Get-Process explorer -ErrorAction SilentlyContinue)) { Start-Process explorer.exe }
    }
}
