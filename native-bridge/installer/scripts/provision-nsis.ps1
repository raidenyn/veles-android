<#
.SYNOPSIS
Provision the pinned NSIS 3.08 compiler for the OTP-25 standalone installers.

.DESCRIPTION
Downloads exactly one archive - the reviewed NSIS 3.08 zip recorded in
../config/tools.lock.json - verifies its SHA-256, rejects any archive entry
that would escape the destination, extracts it and confirms that makensis
reports the locked version.

No WiX toolset, no nsis_tauri_utils.dll and no Tauri crate is downloaded,
expanded or copied. This script is Windows-only (pwsh 7+ on Windows) and is
never used at installer runtime.

.PARAMETER Destination
The directory that receives the extracted NSIS tree. It must be an absolute
path inside the mandated build root build/native-bridge/installer/ (for example
build/native-bridge/installer/tools/nsis), must not already exist, must not
contain '..' segments and must not sit below a reparse point. The archive's own
nsis-3.08/ root is preserved, so the locked compiler path is
<Destination>/nsis-3.08/makensis.exe.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Destination
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Payload the standalone installer toolchain must never contain. The archive is
# already hash-pinned; this denies the known WiX/Tauri names explicitly so a
# future lock edit cannot quietly reintroduce them.
$ForbiddenPayload = @('wix', 'nsis_tauri_utils', 'candle.exe', 'light.exe')

if ($PSVersionTable.PSVersion.Major -lt 7 -or -not $IsWindows) {
    throw 'provision-nsis.ps1 is Windows-only and requires PowerShell 7+ (pwsh)'
}

function Get-RequiredProperty($Object, [string]$Name, [string]$Context) {
    if ($null -eq $Object) { throw "$Context is missing" }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { throw "$Context is missing '$Name'" }
    return $property.Value
}

function Assert-AbsoluteCheckedPath([string]$Path, [string]$ParameterName) {
    if (-not [IO.Path]::IsPathRooted($Path)) {
        throw "$ParameterName must be an absolute path: $Path"
    }
    if (($Path -split '[\\/]') -contains '..') {
        throw "$ParameterName must not contain '..' segments: $Path"
    }
    return [IO.Path]::GetFullPath($Path)
}

function Assert-NoReparsePointAncestor([string]$Path) {
    $current = Split-Path -Parent $Path
    while (-not [string]::IsNullOrEmpty($current)) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "destination ancestor must not be a reparse point: $current"
            }
        }
        $parent = Split-Path -Parent $current
        if ($parent -eq $current) { break }
        $current = $parent
    }
}

# Every generated installer file must stay under build/native-bridge/installer/.
# Compare canonical absolute paths with a separator-terminated prefix so a
# sibling such as build/native-bridge/installer-evil/ cannot match, using
# Windows case-insensitive path semantics.
function Assert-WithinBuildRoot([string]$Path, [string]$BuildRoot) {
    if ($Path.Equals($BuildRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "destination must be a directory below the mandated build root: $BuildRoot"
    }
    $buildRootPrefix = $BuildRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $Path.StartsWith($buildRootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "destination must stay inside the mandated build root $BuildRoot : $Path"
    }
}

$installerDir = Split-Path -Parent $PSScriptRoot
$repositoryRoot = Split-Path -Parent (Split-Path -Parent $installerDir)
$buildRoot = [IO.Path]::GetFullPath((Join-Path $repositoryRoot 'build/native-bridge/installer'))
$lockPath = Join-Path $installerDir 'config/tools.lock.json'
if (-not (Test-Path -LiteralPath $lockPath -PathType Leaf)) {
    throw "missing reviewed tool lock: $lockPath"
}
$lock = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json

$schema = Get-RequiredProperty $lock 'schema' 'tool lock'
if ($schema -ne 1) { throw "unsupported tool lock schema: $schema" }

$archives = Get-RequiredProperty $lock 'archives' 'tool lock'
$archiveNames = @($archives.PSObject.Properties.Name)
if ($archiveNames.Count -ne 1 -or $archiveNames[0] -ne 'nsis') {
    throw "the tool lock must pin exactly one archive, 'nsis'; found: $($archiveNames -join ', ')"
}
$nsis = Get-RequiredProperty $archives 'nsis' 'tool lock archives'
foreach ($field in @('version', 'url', 'sha256', 'archive_file', 'extract_root', 'makensis', 'version_argument')) {
    $null = Get-RequiredProperty $nsis $field "tool lock archive 'nsis'"
}
if ($nsis.url -notmatch '^https://') { throw "locked NSIS url must be https: $($nsis.url)" }
if ($nsis.sha256 -notmatch '^[0-9a-f]{64}$') {
    throw "locked NSIS sha256 must be 64 lowercase hex characters: $($nsis.sha256)"
}
foreach ($field in @('archive_file', 'extract_root', 'makensis')) {
    $value = Get-RequiredProperty $nsis $field "tool lock archive 'nsis'"
    if ($value -match '[\\:]' -or $value.StartsWith('/') -or (($value -split '/') -contains '..')) {
        throw "tool lock archive 'nsis' $field must be a relative archive path without escaping segments: $value"
    }
}
$rootPrefix = $nsis.extract_root + '/'
if (-not $nsis.makensis.StartsWith($rootPrefix)) {
    throw "locked makensis path must live under $rootPrefix : $($nsis.makensis)"
}

$destinationPath = Assert-AbsoluteCheckedPath $Destination '-Destination'
# Containment is enforced on the canonical path before anything is created,
# downloaded or extracted.
Assert-WithinBuildRoot $destinationPath $buildRoot
if (Test-Path -LiteralPath $destinationPath) {
    throw "destination must not already exist: $destinationPath"
}
Assert-NoReparsePointAncestor $destinationPath
$parent = Split-Path -Parent $destinationPath
if ([string]::IsNullOrEmpty($parent)) {
    throw "destination must have a parent directory: $destinationPath"
}
if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
}

$stage = Join-Path $parent ('.nsis-stage-' + [guid]::NewGuid().ToString('N'))
$archive = Join-Path $stage $nsis.archive_file

try {
    New-Item -ItemType Directory -Path $stage | Out-Null
    Invoke-WebRequest -Uri $nsis.url -OutFile $archive
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
    if ($actualHash -ne $nsis.sha256) {
        throw "SHA256 mismatch for $($nsis.archive_file): expected $($nsis.sha256), got $actualHash"
    }

    # Refuse escaping entries and forbidden payloads before anything is written.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($archive)
    try {
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName.Replace('\', '/')
            if ($name.EndsWith('/')) { continue }
            if ([string]::IsNullOrEmpty($name)) { throw 'pinned NSIS archive contains an empty entry name' }
            if ($name.StartsWith('/') -or $name -match '^[A-Za-z]:') {
                throw "pinned NSIS archive contains an absolute path: $name"
            }
            $segments = $name.Split('/')
            if (($segments -contains '..') -or ($segments -contains '.')) {
                throw "pinned NSIS archive contains an escaping path: $name"
            }
            if (-not $name.StartsWith($rootPrefix)) {
                throw "pinned NSIS archive entry escapes $rootPrefix : $name"
            }
            $leaf = $segments[$segments.Count - 1].ToLowerInvariant()
            foreach ($forbidden in $ForbiddenPayload) {
                if ($leaf.Contains($forbidden)) {
                    throw "pinned NSIS archive contains forbidden installer payload: $name"
                }
            }
        }
    }
    finally {
        $zip.Dispose()
    }

    $extract = Join-Path $stage 'extract'
    Expand-Archive -LiteralPath $archive -DestinationPath $extract
    $makensis = Join-Path $extract ($nsis.makensis.Replace('/', [IO.Path]::DirectorySeparatorChar))
    if (-not (Test-Path -LiteralPath $makensis -PathType Leaf)) {
        throw "pinned NSIS archive does not contain $($nsis.makensis)"
    }

    $versionOutput = & $makensis $nsis.version_argument 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        throw "makensis $($nsis.version_argument) exited $LASTEXITCODE : $versionOutput"
    }
    if ($versionOutput -notmatch ([regex]::Escape($nsis.version))) {
        throw "makensis reported '$($versionOutput.Trim())' but the tool lock pins NSIS $($nsis.version)"
    }

    Move-Item -LiteralPath $extract -Destination $destinationPath
    Write-Output "verified NSIS $($nsis.version) ($($nsis.sha256)) at $destinationPath"
}
finally {
    if (Test-Path -LiteralPath $stage) {
        Remove-Item -LiteralPath $stage -Recurse -Force
    }
}
