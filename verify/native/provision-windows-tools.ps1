param(
    [Parameter(Mandatory = $true)]
    [string]$TauriCachePath
)

$ErrorActionPreference = 'Stop'

# The expected cache layout Tauri 2.6.0 reads under
# <cargo target dir>/.tauri/ when bundle.useLocalToolsDir is true:
#   WixTools314/  (the wix314-binaries.zip archive contents, rooted flat)
#   NSIS/         (the nsis-3.08 archive contents, rooted under NSIS/)
#
# The pinned archives do NOT extract into these roots directly:
#   - wix314-binaries.zip extracts FLAT (candle.exe at the archive root, no
#     WixTools314/ prefix), so the provisioner re-roots every WiX entry under
#     WixTools314/ when copying into the cache.
#   - nsis-3.zip extracts under nsis-3.08/ (makensis.exe at
#     nsis-3.08/makensis.exe), so the provisioner strips the nsis-3.08/ prefix
#     and re-roots every NSIS entry under NSIS/ when copying into the cache.
#
# The required set covers every file Tauri 2.6.0's NSIS installer template
# (crates/tauri-bundler/src/bundle/windows/nsis/installer.nsi) transitively
# !includes plus the plugin DLLs its utils.nsh / FileAssociation.nsh use:
#   - installer.nsi unconditionally !includes MUI2.nsh, FileFunc.nsh, x64.nsh,
#     WordFunc.nsh, utils.nsh, FileAssociation.nsh, Win\COM.nsh, Win\Propkey.nsh,
#     StrFunc.nsh.
#   - MUI2.nsh redirects to Contrib\Modern UI 2\MUI2.nsh, which !includes
#     WinMessages.nsh, LogicLib.nsh, nsDialogs.nsh, LangFile.nsh and the whole
#     Contrib\Modern UI 2 tree (Deprecated/Interface/Localization/Pages +
#     Pages\*). It also sets !addincludedir to Contrib\Modern UI 2.
#   - utils.nsh and FileAssociation.nsh use the System and nsDialogs plugins.
#   - Sections.nsh and Util.nsh are standard NSIS headers commonly pulled by
#     the above; include them so the offline build resolves every !include.
# The build uses the default currentUser INSTALLMODE, so MultiUser.nsh is NOT
# required and is intentionally omitted.
# installer.nsi also uses the MUI2 StartMenu page (MUI_PAGE_STARTMENU, which
# loads the StartMenu.dll plugin), MUI_RESERVEFILE_LANGDLL (which reserves
# LangDLL.dll), and the default MUI_LANGUAGE "English" (which requires
# Contrib/Language files/English.nlf and English.nsh). These four files are
# required even though they are not !included by name in installer.nsi.
# Tauri's NSIS template uses solid compression. Its zlib path reads both Unicode
# zlib stubs while compiling, so keep the normal and solid pair below.
$requiredFiles = @(
    'WixTools314/candle.exe', 'WixTools314/candle.exe.config', 'WixTools314/darice.cub',
    'WixTools314/light.exe', 'WixTools314/light.exe.config', 'WixTools314/wconsole.dll',
    'WixTools314/winterop.dll', 'WixTools314/wix.dll', 'WixTools314/WixUIExtension.dll',
    'WixTools314/WixUtilExtension.dll',
    # The root launcher runs Bin/makensis.exe, whose zlib1.dll import must be
    # present next to the compiler for the Windows loader to start it.
    'NSIS/makensis.exe', 'NSIS/Bin/makensis.exe', 'NSIS/Bin/zlib1.dll',
    # `/VERSION` initializes CEXEBuild before it reads an installer script:
    # it selects the default Unicode zlib stub and loads the uninstaller icon.
    'NSIS/Stubs/lzma-x86-unicode', 'NSIS/Stubs/lzma_solid-x86-unicode', 'NSIS/Stubs/uninst',
    'NSIS/Stubs/zlib-x86-unicode', 'NSIS/Stubs/zlib_solid-x86-unicode',
    'NSIS/Plugins/x86-unicode/nsis_tauri_utils.dll',
    'NSIS/Plugins/x86-unicode/additional/nsis_tauri_utils.dll',
    'NSIS/Plugins/x86-unicode/nsDialogs.dll', 'NSIS/Plugins/x86-unicode/System.dll',
    # MUI2 StartMenu page (MUI_PAGE_STARTMENU) loads StartMenu.dll; the MUI
    # language dialog (MUI_RESERVEFILE_LANGDLL) reserves LangDLL.dll.
    'NSIS/Plugins/x86-unicode/StartMenu.dll', 'NSIS/Plugins/x86-unicode/LangDLL.dll',
    'NSIS/Include/MUI2.nsh', 'NSIS/Include/FileFunc.nsh', 'NSIS/Include/x64.nsh',
    'NSIS/Include/nsDialogs.nsh', 'NSIS/Include/WinMessages.nsh',
    'NSIS/Include/WordFunc.nsh', 'NSIS/Include/StrFunc.nsh',
    'NSIS/Include/Win/COM.nsh', 'NSIS/Include/Win/Propkey.nsh',
    'NSIS/Include/LogicLib.nsh', 'NSIS/Include/LangFile.nsh',
    'NSIS/Include/Sections.nsh', 'NSIS/Include/Util.nsh',
    # MUI2.nsh !includes the Contrib\Modern UI 2 tree and adds it as an
    # !addincludedir; every header it pulls must be present offline.
    'NSIS/Contrib/Modern UI 2/MUI2.nsh',
    'NSIS/Contrib/Modern UI 2/Deprecated.nsh',
    'NSIS/Contrib/Modern UI 2/Interface.nsh',
    'NSIS/Contrib/Modern UI 2/Localization.nsh',
    'NSIS/Contrib/Modern UI 2/Pages.nsh',
    'NSIS/Contrib/Modern UI 2/Pages/Components.nsh',
    'NSIS/Contrib/Modern UI 2/Pages/Directory.nsh',
    'NSIS/Contrib/Modern UI 2/Pages/Finish.nsh',
    'NSIS/Contrib/Modern UI 2/Pages/InstallFiles.nsh',
    'NSIS/Contrib/Modern UI 2/Pages/License.nsh',
    'NSIS/Contrib/Modern UI 2/Pages/StartMenu.nsh',
    'NSIS/Contrib/Modern UI 2/Pages/UninstallConfirm.nsh',
    'NSIS/Contrib/Modern UI 2/Pages/Welcome.nsh',
    # The default MUI_LANGUAGE "English" requires the English language files
    # (English.nlf + English.nsh) under Contrib\Language files\.
    'NSIS/Contrib/Language files/English.nlf',
    'NSIS/Contrib/Language files/English.nsh',
    # MUI_INTERFACE (Interface.nsh) defaults MUI_UI to Contrib\UIs\modern.exe
    # and ChangeUI all "${MUI_UI}" reads it at compile time. The Tauri
    # installer.nsi does not override MUI_UI, so the default modern.exe is
    # required. The headerbmp variants are only loaded when MUI_HEADERIMAGE is
    # defined, which Tauri sets only when a header image is configured
    # (Veles does not), so they are intentionally omitted.
    'NSIS/Contrib/UIs/modern.exe',
    # MUI_PAGE_WELCOME (Welcome.nsh) defaults
    # MUI_WELCOMEFINISHPAGE_BITMAP to Contrib\Graphics\Wizard\win.bmp and
    # File-extracts it into $PLUGINSDIR at compile time. The Tauri installer.nsi
    # overrides this only when sidebar_image is configured (Veles does not), so
    # the default win.bmp is required.
    'NSIS/Contrib/Graphics/Wizard/win.bmp',
    # MUI_INTERFACE defaults MUI_ICON to modern-install.ico and MUI_UNICON to
    # modern-uninstall.ico. The Tauri installer.nsi overrides MUI_ICON only
    # when an installer_icon is configured; the default icons are required for
    # the offline preflight compile (which uses MUI2 defaults) and remain
    # available for the real build if it ever falls back to them.
    'NSIS/Contrib/Graphics/Icons/modern-install.ico',
    'NSIS/Contrib/Graphics/Icons/modern-uninstall.ico'
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$manifest = Get-Content (Join-Path $scriptDir 'windows-tools.json') -Raw | ConvertFrom-Json
$cache = [IO.Path]::GetFullPath($TauriCachePath)
$parent = Split-Path -Parent $cache
# The cache parent (e.g. native-bridge/src-tauri/target/) may not exist yet on
# a fresh checkout — cargo would create it during bridgeBuild. Create it here
# so provisioning can run before any build, but require the grandparent to exist
# as a guard against a typo'd TauriCachePath pointing outside the repo.
$grandparent = Split-Path -Parent $parent
if (-not (Test-Path -LiteralPath $grandparent -PathType Container)) { throw "cache grandparent does not exist: $grandparent" }
if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
    New-Item -ItemType Directory -Path $parent | Out-Null
}
if (Test-Path -LiteralPath $cache) { throw "isolated cache must not already exist: $cache" }
$stage = Join-Path $parent ('.tauri-tools-' + [guid]::NewGuid().ToString('N'))
$downloads = Join-Path $stage 'downloads'
$unpacked = Join-Path $stage 'unpacked'
$cacheStage = Join-Path $stage 'cache'

# Map an expected cache path to its actual location under $unpacked by
# replacing the cache root prefix with the archive's real root prefix.
#   WixTools314/<rest> -> <rest>                       (flat archive)
#   NSIS/<rest>        -> nsis-3.08/<rest>             (nsis-3.08-prefixed archive)
function Resolve-ArchiveSource([string]$cachePath) {
    if ($cachePath -like 'WixTools314/*') {
        return Join-Path $unpacked $cachePath.Substring('WixTools314/'.Length)
    }
    if ($cachePath -like 'NSIS/*') {
        return Join-Path $unpacked (Join-Path 'nsis-3.08' $cachePath.Substring('NSIS/'.Length))
    }
    throw "unexpected required cache path root: $cachePath"
}

function Get-VerifiedFile([object]$tool, [string]$name) {
    $destination = Join-Path $downloads $name
    Invoke-WebRequest -Uri $tool.url -OutFile $destination
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $destination).Hash.ToLowerInvariant()
    if ($actual -ne $tool.sha256) { throw "SHA256 mismatch for $name" }
    return $destination
}

function Expand-VerifiedArchive([string]$archive, [string]$destination) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($archive)
    try {
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName.Replace('\', '/')
            if ($name.StartsWith('/') -or $name.Split('/').Contains('..')) { throw "unexpected archive path: $name" }
        }
    } finally { $zip.Dispose() }
    Expand-Archive -LiteralPath $archive -DestinationPath $destination
}

try {
    New-Item -ItemType Directory -Path $downloads | Out-Null
    $wix = Get-VerifiedFile $manifest.wix 'wix.zip'
    $nsis = Get-VerifiedFile $manifest.nsis 'nsis.zip'
    $plugin = Get-VerifiedFile $manifest.nsisTauriUtils 'nsis_tauri_utils.dll'
    Expand-VerifiedArchive $wix $unpacked
    Expand-VerifiedArchive $nsis $unpacked
    foreach ($path in $requiredFiles | Where-Object { $_ -notmatch 'nsis_tauri_utils\.dll$' }) {
        $source = Resolve-ArchiveSource $path
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "missing Tauri archive file: $path (looked at $source)" }
        $destination = Join-Path $cacheStage $path
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
        Copy-Item -LiteralPath $source -Destination $destination
    }
    foreach ($path in $requiredFiles | Where-Object { $_ -match 'nsis_tauri_utils\.dll$' }) {
        $destination = Join-Path $cacheStage $path
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
        Copy-Item -LiteralPath $plugin -Destination $destination
    }
    $actualFiles = Get-ChildItem -LiteralPath $cacheStage -File -Recurse | ForEach-Object {
        $_.FullName.Substring($cacheStage.Length + 1).Replace('\', '/')
    } | Sort-Object
    $unexpected = Compare-Object $requiredFiles $actualFiles -PassThru | Where-Object { $_ -in $actualFiles }
    $missing = Compare-Object $requiredFiles $actualFiles -PassThru | Where-Object { $_ -in $requiredFiles }
    if ($unexpected) { throw "unexpected Tauri cache files: $($unexpected -join ', ')" }
    if ($missing) { throw "missing Tauri cache files: $($missing -join ', ')" }
    Move-Item -LiteralPath $cacheStage -Destination $cache
} finally {
    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
}
