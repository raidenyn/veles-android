param(
    [Parameter(Mandatory = $true)] [string]$TauriCachePath,
    [string[]]$AcquireCommand = @('.\gradlew.bat', 'bridgeBuild'),
    [string[]]$PackageCommand = @('.\gradlew.bat', 'bridgePackage')
)

$ErrorActionPreference = 'Stop'

foreach ($name in 'ImageOS', 'ImageVersion', 'RUNNER_ARCH') {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) { throw "$name is required" }
}
if ($env:ImageOS -ne 'windows-2025') { throw "expected windows-2025, got $env:ImageOS" }
if ((node --version) -ne 'v26.8.1') { throw 'Node version drift' }
if ((npm --version) -ne '11.19.0') { throw 'npm version drift' }

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $scriptDir 'provision-windows-tools.ps1') -TauriCachePath $TauriCachePath
$env:TAURI_WIX_PATH = Join-Path $TauriCachePath 'WixTools314'
$env:TAURI_NSIS_PATH = Join-Path $TauriCachePath 'NSIS'
& $AcquireCommand[0] $AcquireCommand[1..($AcquireCommand.Count - 1)]
if ($LASTEXITCODE -ne 0) { throw "acquisition command failed: $LASTEXITCODE" }
$probe = 'https://github.com/'
Invoke-WebRequest -Uri $probe -UseBasicParsing | Out-Null

$rule = 'VelesTargetRunnerOffline-' + [guid]::NewGuid().ToString('N')
try {
    New-NetFirewallRule -DisplayName $rule -Direction Outbound -Action Block -Profile Any | Out-Null
    if (-not (Get-NetFirewallRule -DisplayName $rule -ErrorAction Stop | Where-Object Enabled -eq 'True')) { throw 'outbound deny rule is not active' }
    if (Invoke-WebRequest -Uri $probe -UseBasicParsing -ErrorAction SilentlyContinue) { throw 'network probe succeeded after outbound denial' }
    $env:CARGO_NET_OFFLINE = 'true'
    & $PackageCommand[0] $PackageCommand[1..($PackageCommand.Count - 1)]
    if ($LASTEXITCODE -ne 0) { throw "package command failed: $LASTEXITCODE" }
} finally {
    Remove-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue
}
