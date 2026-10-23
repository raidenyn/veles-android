$ErrorActionPreference = 'Stop'

if ($args.Count -ne 4) {
  Write-Error 'usage: prepare-run.ps1 <output.tar> <resolved-commit> <product-dir> <view-dir>'
  exit 2
}

foreach ($name in 'ImageOS', 'ImageVersion', 'RUNNER_ARCH') {
  if ([string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($name))) {
    Write-Error "$name is required"
    exit 2
  }
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
node (Join-Path $scriptDir 'create-run.mjs') $args[0] $args[1] $env:ImageOS $env:ImageVersion $env:RUNNER_ARCH $args[2] $args[3]
