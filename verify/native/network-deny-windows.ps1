param(
    [Parameter(Mandatory = $true)] [string]$TauriCachePath,
    [string[]]$AcquireCommand = @('.\gradlew.bat', 'bridgeBuild'),
    [string[]]$PackageCommand = @('.\gradlew.bat', 'bridgePackage')
)

$ErrorActionPreference = 'Stop'

# Environment/usage/identity failures exit 2 (never 1); artifact mismatches are
# the responsibility of the caller's byte comparison, not this wrapper. A `throw`
# under ErrorActionPreference=Stop would otherwise surface as exit 1, so every
# throw path is wrapped to set the process exit code to 2 explicitly.
function env-fail {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Error $Message
    exit 2
}

foreach ($name in 'ImageOS', 'ImageVersion', 'RUNNER_ARCH') {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) { env-fail "$name is required" }
}
if ($env:ImageOS -ne 'win25') { env-fail "expected win25, got $env:ImageOS" }
if ((node --version) -ne 'v26.8.1') { env-fail 'Node version drift' }
if ((npm --version) -ne '11.19.0') { env-fail 'npm version drift' }

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $scriptDir 'provision-windows-tools.ps1') -TauriCachePath $TauriCachePath
if ($LASTEXITCODE -ne 0) { env-fail "provisioning failed: $LASTEXITCODE" }
# Tauri 2.6.0 locates WiX under <cargo target dir>/.tauri/WixTools314 and NSIS
# under <cargo target dir>/.tauri/NSIS when bundle.useLocalToolsDir is true.
# The workflow passes TauriCachePath = native-bridge/src-tauri/target/.tauri so
# the provisioned cache is exactly where Tauri reads. NSIS_PATH is forwarded as
# a belt-and-suspenders override (Tauri's NSIS bundler reads NSIS_PATH, not
# TAURI_NSIS_PATH); WiX has no env override and relies on useLocalToolsDir.
$env:NSIS_PATH = Join-Path $TauriCachePath 'NSIS'
& $AcquireCommand[0] $AcquireCommand[1..($AcquireCommand.Count - 1)]
if ($LASTEXITCODE -ne 0) { env-fail "acquisition command failed: $LASTEXITCODE" }
$probe = 'https://github.com/'
Invoke-WebRequest -Uri $probe -UseBasicParsing | Out-Null

$rule = 'VelesTargetRunnerOffline-' + [guid]::NewGuid().ToString('N')
try {
    New-NetFirewallRule -DisplayName $rule -Direction Outbound -Action Block -Profile Any | Out-Null
    if (-not (Get-NetFirewallRule -DisplayName $rule -ErrorAction Stop | Where-Object Enabled -eq 'True')) { env-fail 'outbound deny rule is not active' }
    if (Invoke-WebRequest -Uri $probe -UseBasicParsing -ErrorAction SilentlyContinue) { env-fail 'network probe succeeded after outbound denial' }
    $env:CARGO_NET_OFFLINE = 'true'
    & $PackageCommand[0] $PackageCommand[1..($PackageCommand.Count - 1)]
    if ($LASTEXITCODE -ne 0) { env-fail "package command failed: $LASTEXITCODE" }
} finally {
    Remove-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue
}
