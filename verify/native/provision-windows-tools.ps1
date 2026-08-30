param(
    [Parameter(Mandatory = $true)]
    [string]$TauriCachePath
)

$ErrorActionPreference = 'Stop'

$requiredFiles = @(
    'WixTools314/candle.exe', 'WixTools314/candle.exe.config', 'WixTools314/darice.cub',
    'WixTools314/light.exe', 'WixTools314/light.exe.config', 'WixTools314/wconsole.dll',
    'WixTools314/winterop.dll', 'WixTools314/wix.dll', 'WixTools314/WixUIExtension.dll',
    'WixTools314/WixUtilExtension.dll', 'NSIS/makensis.exe', 'NSIS/Bin/makensis.exe',
    'NSIS/Stubs/lzma-x86-unicode', 'NSIS/Stubs/lzma_solid-x86-unicode',
    'NSIS/Plugins/x86-unicode/nsis_tauri_utils.dll',
    'NSIS/Plugins/x86-unicode/additional/nsis_tauri_utils.dll', 'NSIS/Include/MUI2.nsh',
    'NSIS/Include/FileFunc.nsh', 'NSIS/Include/x64.nsh', 'NSIS/Include/nsDialogs.nsh',
    'NSIS/Include/WinMessages.nsh'
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$manifest = Get-Content (Join-Path $scriptDir 'windows-tools.json') -Raw | ConvertFrom-Json
$cache = [IO.Path]::GetFullPath($TauriCachePath)
$parent = Split-Path -Parent $cache
if (-not (Test-Path -LiteralPath $parent -PathType Container)) { throw "cache parent does not exist: $parent" }
if (Test-Path -LiteralPath $cache) { throw "isolated cache must not already exist: $cache" }
$stage = Join-Path $parent ('.tauri-tools-' + [guid]::NewGuid().ToString('N'))
$downloads = Join-Path $stage 'downloads'
$unpacked = Join-Path $stage 'unpacked'
$cacheStage = Join-Path $stage 'cache'

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
        $source = Join-Path $unpacked $path
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "missing Tauri archive file: $path" }
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
