<#
.SYNOPSIS
    Removes the per-user OTP-23 feasibility registration(s) and their files.
.DESCRIPTION
    Deletes only the two feasibility host names under HKCU and the install root; it never
    touches any other Native Messaging host.
#>
[CmdletBinding()]
param(
    [ValidateSet('plain', 'tauri', 'all')][string]$Flavour = 'all',
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Veles\Otp23Feasibility')
)
$ErrorActionPreference = 'Stop'
$names = @{ plain = 'me.nagaev.veles.otp23.plain'; tauri = 'me.nagaev.veles.otp23.tauri' }
$selected = if ($Flavour -eq 'all') { $names.Keys } else { @($Flavour) }
foreach ($flavour in $selected) {
    $hostName = $names[$flavour]
    $key = "HKCU:\Software\Google\Chrome\NativeMessagingHosts\$hostName"
    if (Test-Path $key) { Remove-Item -Path $key -Recurse -Force; "removed $key" } else { "absent  $key" }
    $manifest = Join-Path $InstallRoot "$hostName.json"
    if (Test-Path $manifest) { Remove-Item -LiteralPath $manifest -Force; "removed $manifest" }
    $dir = Join-Path $InstallRoot $flavour
    if (Test-Path $dir) { Remove-Item -LiteralPath $dir -Recurse -Force; "removed $dir" }
}
if ((Test-Path $InstallRoot) -and -not (Get-ChildItem -LiteralPath $InstallRoot -Force)) {
    Remove-Item -LiteralPath $InstallRoot -Force
    "removed empty $InstallRoot"
}
