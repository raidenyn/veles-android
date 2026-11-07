<#
.SYNOPSIS
Credential-handling shim for the Windows signing stages (OTP-25 Task 13).

.DESCRIPTION
sign-windows.ps1 -Stage nested|outer -Inputs DIR -Output DIR -Scratch DIR -Reports DIR
                 -Descriptor FILE -Boundary FILE -Commit SHA -RecipeDigest HEX

All logic - authorization, credential-presence check, input provenance, the ephemeral
certificate import, Authenticode signing and timestamping, verification, the Task 9 PE
comparison, nested-hash verification and guaranteed cleanup - lives in release_gate.py.
This shim only locates signtool.exe and the interpreter and forwards the arguments.
Credentials are REFERENCES in the environment (VELES_WIN_*); they are never expanded, echoed
or placed on this script's command line. There is no unsigned fallback and no option that skips
a check. It compiles nothing and publishes nothing.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet('nested', 'outer')][string]$Stage,
    [Parameter(Mandatory = $true)][string]$Inputs,
    [Parameter(Mandatory = $true)][string]$Output,
    [Parameter(Mandatory = $true)][string]$Scratch,
    [Parameter(Mandatory = $true)][string]$Reports,
    [Parameter(Mandatory = $true)][string]$Descriptor,
    [Parameter(Mandatory = $true)][string]$Boundary,
    [Parameter(Mandatory = $true)][string]$Commit,
    [Parameter(Mandatory = $true)][string]$RecipeDigest
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$gate = Join-Path $PSScriptRoot 'release_gate.py'
if (-not (Test-Path -LiteralPath $gate -PathType Leaf)) { throw 'release_gate.py not found' }

$python = Get-Command python -ErrorAction Stop
if (-not (Get-Command signtool.exe -ErrorAction Ignore)) {
    # The Windows SDK signtool is not on PATH on hosted runners: take the newest x64 one.
    $kits = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
    $candidate = Get-ChildItem -LiteralPath $kits -Directory -ErrorAction Stop |
        Where-Object { $_.Name -match '^10\.' } |
        Sort-Object { [version]$_.Name } -Descending |
        ForEach-Object { Join-Path $_.FullName 'x64\signtool.exe' } |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
        Select-Object -First 1
    if (-not $candidate) { throw 'signtool.exe not found' }
    $env:PATH = (Split-Path -Parent $candidate) + [IO.Path]::PathSeparator + $env:PATH
}

# -I: ignore PYTHON* variables and the current directory, so nothing in the working tree can shadow a module.
& $python.Source -I $gate sign --platform windows --stage $Stage --inputs $Inputs --output $Output `
    --scratch $Scratch --reports $Reports --descriptor $Descriptor --boundary $Boundary `
    --commit $Commit --recipe-digest $RecipeDigest
exit $LASTEXITCODE
