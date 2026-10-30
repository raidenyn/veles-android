<#
.SYNOPSIS
    Registers an OTP-23 feasibility Native Messaging host for the CURRENT USER only.

.DESCRIPTION
    Writes a host manifest with an absolute executable path and the exact-origin
    allowed_origins entry, then points HKCU\Software\Google\Chrome\NativeMessagingHosts\<name>
    at it. Needs no administrator rights, writes nothing machine-wide and installs no service,
    scheduled task or autostart entry. Remove it again with unregister-host.ps1.

.PARAMETER Flavour
    plain  -> me.nagaev.veles.otp23.plain  (Rust-only host)
    tauri  -> me.nagaev.veles.otp23.tauri  (Tauri-linked host)

.PARAMETER ExePath
    Built host executable. It is copied into the install root so rebuilds never lock the
    registered binary.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('plain', 'tauri')][string]$Flavour,
    [Parameter(Mandatory)][string]$ExePath,
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Veles\Otp23Feasibility')
)
$ErrorActionPreference = 'Stop'

$identity = Get-Content -Raw (Join-Path $PSScriptRoot '..\src\identity.rs')
function Read-Constant([string]$name) {
    if ($identity -notmatch "pub const $name`: &str = `"([^`"]+)`";") { throw "constant $name not found in identity.rs" }
    $Matches[1]
}
$extensionId = Read-Constant 'EXTENSION_ID'
$origin = Read-Constant 'EXPECTED_ORIGIN'
$hostName = if ($Flavour -eq 'plain') { Read-Constant 'HOST_NAME_PLAIN' } else { Read-Constant 'HOST_NAME_TAURI' }
if ($origin -ne "chrome-extension://$extensionId/") { throw 'EXPECTED_ORIGIN does not match EXTENSION_ID' }
if ($extensionId -notmatch '^[a-p]{32}$') { throw 'EXTENSION_ID is not a 32-character a-p Chrome id' }

$exe = (Resolve-Path -LiteralPath $ExePath).Path
$targetDir = Join-Path $InstallRoot $Flavour
New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
$installed = Join-Path $targetDir (Split-Path -Leaf $exe)
Copy-Item -LiteralPath $exe -Destination $installed -Force
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $installed).Hash.ToLowerInvariant()

$manifestPath = Join-Path $InstallRoot "$hostName.json"
$manifest = [ordered]@{
    name            = $hostName
    description     = "Veles OTP-23 feasibility host ($Flavour); synthetic bytes only"
    path            = $installed
    type            = 'stdio'
    allowed_origins = @($origin)
}
# UTF-8 without BOM: Chrome rejects a BOM.
[System.IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 3), (New-Object System.Text.UTF8Encoding($false)))

$key = "HKCU:\Software\Google\Chrome\NativeMessagingHosts\$hostName"
New-Item -Path $key -Force | Out-Null
Set-ItemProperty -Path $key -Name '(default)' -Value $manifestPath

[pscustomobject]@{
    HostName     = $hostName
    Manifest     = $manifestPath
    Executable   = $installed
    Sha256       = $hash
    Origin       = $origin
    RegistryKey  = $key
    Scope        = 'HKCU (current user only)'
} | Format-List
