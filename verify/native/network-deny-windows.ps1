# Parameters are declared OPTIONAL/nullable so a mandatory-binding failure
# (which happens BEFORE $ErrorActionPreference and the outer try/catch take
# effect, surfacing as exit 1) cannot escape the 0/1/2 contract. They are
# validated inside the guarded body below; a missing/invalid parameter exits 2
# via env-fail like every other usage/environment failure.
param(
    [string]$TauriCachePath,
    [string[]]$AcquireCommand = @('.\gradlew.bat', 'bridgeBuild'),
    [string[]]$PackageCommand = @('.\gradlew.bat', 'bridgePackage')
)

$ErrorActionPreference = 'Stop'

# Environment/usage/identity failures exit 2 (never 1); artifact mismatches are
# the responsibility of the caller's byte comparison, not this wrapper. Under
# ErrorActionPreference=Stop a `throw` surfaces as exit 1, and Write-Error
# becomes a terminating error that also unwinds without running the following
# `exit 2`. So env-fail writes a non-terminating error to stderr (the message)
# and then exits 2 explicitly; every failure path routes through env-fail or a
# guarded try/catch that sets $LASTEXITCODE-based exit 2.
function env-fail {
    param([Parameter(Mandatory = $true)][string]$Message)
    [Console]::Error.WriteLine("env-fail: $Message")
    $host.SetShouldExit(2)
    exit 2
}

try {
    if ([string]::IsNullOrWhiteSpace($TauriCachePath)) { env-fail 'TauriCachePath is required' }
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
    try {
        Invoke-WebRequest -Uri $probe -UseBasicParsing | Out-Null
    } catch {
        env-fail "pre-denial network probe failed: $($_.Exception.Message)"
    }

    $rule = 'VelesTargetRunnerOffline-' + [guid]::NewGuid().ToString('N')
    try {
        New-NetFirewallRule -DisplayName $rule -Direction Outbound -Action Block -Profile Any | Out-Null
        if (-not (Get-NetFirewallRule -DisplayName $rule -ErrorAction Stop | Where-Object Enabled -eq 'True')) { env-fail 'outbound deny rule is not active' }
        $probeSucceeded = $false
        try {
            Invoke-WebRequest -Uri $probe -UseBasicParsing -ErrorAction Stop | Out-Null
            $probeSucceeded = $true
        } catch {
            # Expected: the deny rule blocks the probe. AWebRequest throws under
            # Stop; swallow it and treat as the required probe failure.
        }
        if ($probeSucceeded) { env-fail 'network probe succeeded after outbound denial' }
        $env:CARGO_NET_OFFLINE = 'true'
        & $PackageCommand[0] $PackageCommand[1..($PackageCommand.Count - 1)]
        if ($LASTEXITCODE -ne 0) { env-fail "package command failed: $LASTEXITCODE" }
    } finally {
        Remove-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue
    }
} catch {
    # Any terminating error that escaped a guarded path above is an
    # environment/usage/tool failure (artifact mismatch is the caller's job),
    # so normalize it to exit 2 rather than letting PowerShell surface exit 1.
    env-fail "unhandled terminating error: $($_.Exception.Message)"
}
