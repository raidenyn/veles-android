<#
.SYNOPSIS
Register a separately compiled DEVELOPMENT native bridge host for an unpacked
extension (OTP-25 Task 12). Developer registration only: not distribution
signature acceptance, and never the production host.

.DESCRIPTION
The development channel (me.nagaev.veles.bridge.dev, NativeBridgeDev) is selected
inside the dev-compiled maintenance engine. There is no parameter for a root, host
name, origin, channel or manifest. All validation lives in development.py, which
this script runs; it writes no registry key and deletes nothing itself.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$ExtensionId,
    [Parameter(Mandatory = $true)] [string]$HostBinary
)
$ErrorActionPreference = 'Stop'
$python = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
if ($null -eq $python) { [Console]::Error.WriteLine('development: python 3.12+ is required'); exit 1 }
& $python.Source -I (Join-Path $PSScriptRoot 'development.py') register --extension-id $ExtensionId --host-binary $HostBinary
exit $LASTEXITCODE
