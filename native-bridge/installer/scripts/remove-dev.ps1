<#
.SYNOPSIS
Remove the DEVELOPMENT native bridge registration (OTP-25 Task 12) through the same
dev-compiled maintenance engine, with the engine's ownership safeguards.

.DESCRIPTION
This script deletes nothing itself and the production registration is never an
input. All validation lives in development.py.
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
& $python.Source -I (Join-Path $PSScriptRoot 'development.py') remove --extension-id $ExtensionId --host-binary $HostBinary
exit $LASTEXITCODE
