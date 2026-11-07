<#
.SYNOPSIS
Generate the UNSIGNED NSIS uninstaller for one Windows producer slot (OTP-25 Task 14).

.DESCRIPTION
Runs the pinned makensis in packaging-time mode (/DGENERATE_UNINSTALLER, see
../windows/installer.nsi), runs the generator once so it writes uninstall.exe next to itself, and
copies that file to -Output. The uninstaller is later signed by Task 13's protected job; nothing is
signed here. No network is used: -Makensis must be the pinned compiler provisioned by
provision-nsis.ps1 under build/native-bridge/installer/tools/nsis.

STATUS: NOT RUN on the development host (no Windows runner, no pwsh, no makensis). Its shape is pinned
by scripts/test_build_contract.py; its behaviour is Windows-runner evidence.

.PARAMETER Makensis
Absolute path to the provisioned nsis-3.08/makensis.exe (must lie under build/native-bridge/installer/tools).

.PARAMETER Version
The release version (MAJOR.MINOR.PATCH) shown in Installed Apps.

.PARAMETER Output
The uninstall.exe to create; it must not exist.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$Makensis,
    [Parameter(Mandatory = $true)] [string]$Version,
    [Parameter(Mandatory = $true)] [string]$Output
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($PSVersionTable.PSVersion.Major -lt 7 -or -not $IsWindows) {
    throw 'generate-uninstaller.ps1 is Windows-only and requires PowerShell 7+ (pwsh)'
}
if ($Version -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') { throw "invalid version: $Version" }
if (-not [IO.Path]::IsPathRooted($Makensis) -or -not [IO.Path]::IsPathRooted($Output)) { throw 'Makensis and Output must be absolute paths' }
$tools = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..\build\native-bridge\installer\tools'))
if (-not ([IO.Path]::GetFullPath($Makensis)).StartsWith($tools + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "makensis must be the pinned compiler under $tools"
}
if (-not (Test-Path -LiteralPath $Makensis -PathType Leaf)) { throw "makensis is missing: $Makensis" }
if (Test-Path -LiteralPath $Output) { throw "output must not exist: $Output" }

$script = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\windows\installer.nsi')).ProviderPath
$work = Join-Path ([IO.Path]::GetTempPath()) ("veles-uninstaller-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $generator = Join-Path $work 'generator.exe'
    $inputs = Join-Path $work 'inputs'
    New-Item -ItemType Directory -Path $inputs | Out-Null
    & $Makensis /V2 "/NOCD" "/DGENERATE_UNINSTALLER" "/DVERSION=$Version" "/DINPUT_DIR=$inputs" "/DOUTFILE=$generator" $script
    if ($LASTEXITCODE -ne 0) { throw "makensis failed with exit code $LASTEXITCODE" }
    & $generator
    if ($LASTEXITCODE -ne 0) { throw "the uninstaller generator failed with exit code $LASTEXITCODE" }
    $generated = Join-Path $work 'uninstall.exe'
    if (-not (Test-Path -LiteralPath $generated -PathType Leaf) -or (Get-Item -LiteralPath $generated).Length -eq 0) {
        throw 'the generator did not write uninstall.exe'
    }
    Copy-Item -LiteralPath $generated -Destination $Output
}
finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction Stop
}
