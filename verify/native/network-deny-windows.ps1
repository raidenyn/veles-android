# Parameters are declared OPTIONAL/nullable so a mandatory-binding failure
# (which happens BEFORE $ErrorActionPreference takes effect, surfacing as
# exit 1) cannot escape the 0/1/2 contract. They are validated inside the
# guarded body below; a missing/invalid parameter exits 2 via env-fail like
# every other usage/environment failure.
#
# EXIT-CONTRACT DESIGN: every failure path performs a TOP-LEVEL `exit 2`.
# GitHub Actions invokes pwsh via a `-command ". '<script>'"`-style wrapper,
# and the host's should-exit hook + stderr writes interact badly with that
# wrapper when an `exit` is issued from inside a try/catch, surfacing as
# exit 1 instead of the intended 2. To keep the 0/1/2 contract:
#   * There is NO outer try/catch around the whole body.
#   * The host's should-exit hook is never invoked.
#   * `env-fail` writes the message to stderr and `exit 2`s. It is only ever
#     called from the script's TOP LEVEL (never from inside a try/catch), so
#     the exit cannot be intercepted.
#   * The only try/catch blocks (around the web-request probes and the
#     firewall rule) set a flag or capture an error message and then `exit 2`
#     directly from their catch handlers — but to be maximally defensive, the
#     catch handlers only record state, and the resulting `env-fail`/`exit 2`
#     call happens AFTER the try/catch at top level. The firewall block's
#     `finally` only cleans up; it never exits.
param(
    [string]$TauriCachePath,
    [string[]]$AcquireCommand = @('.\gradlew.bat', 'bridgeBuild'),
    [string[]]$PackageCommand = @('.\gradlew.bat', 'bridgePackage')
)

$ErrorActionPreference = 'Stop'

# Environment/usage/identity failures exit 2 (never 1); artifact mismatches are
# the responsibility of the caller's byte comparison, not this wrapper. env-fail
# writes a non-terminating message to stderr and then `exit 2`s at TOP LEVEL.
# It is never called from inside a try/catch in this script (see the header
# design note). Under ErrorActionPreference=Stop a `throw` would surface as
# exit 1, so env-fail deliberately avoids throw and uses exit 2.
function env-fail {
    param([Parameter(Mandatory = $true)][string]$Message)
    [Console]::Error.WriteLine("env-fail: $Message")
    exit 2
}

if ([string]::IsNullOrWhiteSpace($TauriCachePath)) { env-fail 'TauriCachePath is required' }
foreach ($name in 'ImageOS', 'ImageVersion', 'RUNNER_ARCH') {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) { env-fail "$name is required" }
}
# ImageOS for the windows-2025 runner label is `win25-vs2026` (the runner label
# `windows-2025` is a versioned alias, not the ImageOS value). The workflow
# `runs-on: windows-2025` label stays; only the wrapper's ImageOS pin is
# updated to the observed value.
if ($env:ImageOS -ne 'win25-vs2026') { env-fail "expected win25-vs2026, got $env:ImageOS" }
if ((node --version) -ne 'v26.8.1') { env-fail 'Node version drift' }
if ((npm --version) -ne '11.19.0') { env-fail 'npm version drift' }

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$tauriCache = [IO.Path]::GetFullPath($TauriCachePath)
& (Join-Path $scriptDir 'provision-windows-tools.ps1') -TauriCachePath $tauriCache
if ($LASTEXITCODE -ne 0) { env-fail "provisioning failed: $LASTEXITCODE" }
# Tauri 2.6.0 locates WiX under <cargo target dir>/.tauri/WixTools314 and NSIS
# under <cargo target dir>/.tauri/NSIS when bundle.useLocalToolsDir is true.
# The workflow passes TauriCachePath = native-bridge/src-tauri/target/.tauri.
# Gradle launches Tauri from native-bridge/, so NSIS_PATH must use the
# canonical cache path rather than that relative workflow argument; otherwise
# makensis is looked up under native-bridge/native-bridge/... and fails with
# "The system cannot find the file specified". Tauri's NSIS bundler reads
# NSIS_PATH (not TAURI_NSIS_PATH); WiX has no env override and relies on
# useLocalToolsDir.
$env:NSIS_PATH = Join-Path $tauriCache 'NSIS'
$makensis = Join-Path $env:NSIS_PATH 'makensis.exe'
$makensisOutput = ''
$makensisErrorMessage = $null
try {
    $makensisOutput = & $makensis /VERSION 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { $makensisErrorMessage = $makensisOutput }
} catch {
    $makensisErrorMessage = "$($_.Exception.Message)`n$makensisOutput"
}
if ($null -ne $makensisErrorMessage) { env-fail "makensis preflight failed: $makensisErrorMessage" }
& $AcquireCommand[0] $AcquireCommand[1..($AcquireCommand.Count - 1)]
if ($LASTEXITCODE -ne 0) { env-fail "acquisition command failed: $LASTEXITCODE" }

$probe = 'https://github.com/'
$probeErrorMessage = $null
try {
    Invoke-WebRequest -Uri $probe -UseBasicParsing | Out-Null
} catch {
    $probeErrorMessage = $_.Exception.Message
}
# env-fail called at TOP LEVEL (outside the try/catch above) so its exit 2
# cannot be swallowed by the catch handler.
if ($null -ne $probeErrorMessage) { env-fail "pre-denial network probe failed: $probeErrorMessage" }

$rule = 'VelesTargetRunnerOffline-' + [guid]::NewGuid().ToString('N')
$firewallErrorMessage = $null
$ruleActive = $false
$probeSucceededAfterDeny = $false
$packageExitCode = 0
try {
    New-NetFirewallRule -DisplayName $rule -Direction Outbound -Action Block -Profile Any | Out-Null
    $ruleActive = $null -ne (Get-NetFirewallRule -DisplayName $rule -ErrorAction Stop | Where-Object Enabled -eq 'True')
    if (-not $ruleActive) {
        # Defer env-fail to after the try so the exit is top-level; record
        # state and let the finally clean up first.
        $firewallErrorMessage = 'outbound deny rule is not active'
    } else {
        try {
            Invoke-WebRequest -Uri $probe -UseBasicParsing -ErrorAction Stop | Out-Null
            $probeSucceededAfterDeny = $true
        } catch {
            # Expected: the deny rule blocks the probe. Invoke-WebRequest
            # throws under Stop; swallow it and treat as the required probe
            # failure (probeSucceededAfterDeny stays $false).
        }
        if ($probeSucceededAfterDeny) {
            $firewallErrorMessage = 'network probe succeeded after outbound denial'
        } else {
            $env:CARGO_NET_OFFLINE = 'true'
            & $PackageCommand[0] $PackageCommand[1..($PackageCommand.Count - 1)]
            $packageExitCode = $LASTEXITCODE
        }
    }
} catch {
    # A terminating error escaping the guarded firewall commands above is an
    # environment/tool failure (artifact mismatch is the caller's job);
    # record it and exit 2 at top level after the finally cleans up.
    $firewallErrorMessage = "unhandled firewall-block error: $($_.Exception.Message)"
} finally {
    Remove-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue
}
# All env-fail calls below are at TOP LEVEL (after the try/finally), so their
# exit 2 cannot be swallowed by any catch handler.
if ($null -ne $firewallErrorMessage) { env-fail $firewallErrorMessage }
if ($packageExitCode -ne 0) { env-fail "package command failed: $packageExitCode" }
