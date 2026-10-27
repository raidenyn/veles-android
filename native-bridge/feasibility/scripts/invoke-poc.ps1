<#
.SYNOPSIS
    Runs the OTP-23 plain-Rust PoC validation in the interactive Windows session.

.DESCRIPTION
    Direct mode (default): registers the plain Native Messaging host (HKCU only) for
    target\release\otp23-host.exe, runs the selected Python driver from the feasibility
    directory, always records evidence\poc-run-status.json with UTC start/finish and the
    exit code (also on failure, which keeps its nonzero code), then tears down only its own
    host registration via unregister-host.ps1 -Flavour plain. It kills no processes and
    cleans nothing else. Logs via Start/Stop-Transcript into evidence\tmp-poc-transcript.txt.

    Scheduled mode (-Schedule, e.g. from an SSH session): registers a temporary current-user
    scheduled task (LogonType Interactive, RunLevel Limited, no credentials) whose action is
    this same wrapper WITHOUT -Schedule, so the run happens inside the desktop session. The
    task is started and the name returned immediately as JSON; the remote caller polls
    evidence\poc-run-status.json and removes the exact task when it is done. This wrapper
    never waits for the run. Before starting the task, this mode removes only this
    wrapper's own stale evidence\poc-run-status.json (and its .tmp), so the caller cannot
    read a previous run's result; no other cleanup happens.

.PARAMETER Schedule
    Register and start the temporary scheduled task instead of running directly.

.PARAMETER Script
    Python driver filename under scripts\ (allowlist: physical_validate.py, poc_background.py,
    strict_background.py). strict_background.py has its own CLI: it takes --scenario (never --flavour),
    so the wrapper passes `--scenario <Scenario> --out <OutPath>` plus the optional adb arguments below and
    nothing else. The other two drivers keep their exact previous command lines.

.PARAMETER Scenario
    cold | prewarm. Required with -Script strict_background.py and rejected otherwise. The two scenarios are
    separate runs; run them one after the other, never combined.

.PARAMETER AdbPath
    Optional adb executable for strict_background.py (--adb-path). Quoted as one argument; must not contain
    a double quote.

.PARAMETER PhoneClockOffsetMs
    Measured desktop-minus-phone clock offset, with -PhoneClockUncertaintyMs and -PhoneClockSource (free text,
    no quotes). Without all three the phone-evidence check stays BLOCKED (perfect clocks are never assumed).

.PARAMETER ForegroundWaitSeconds
    Optional bounded wait (strict_background.py --foreground-wait-s; 0 keeps the driver default of 180) for a
    human to physically click the test Chrome window so it is the OS foreground before any BLE work.

.PARAMETER PhoneSerials
    Optional comma-separated adb serials (one --adb each) the phones are attached to on THIS machine; the
    status file records only the count, never the serials. Safe characters only (A-Z a-z 0-9 . _ : -).
    Passed as one string because PowerShell -File does not bind comma lists to string arrays.

.PARAMETER OutPath
    Evidence output path handed to the driver's --out (default evidence\poc-physical.json;
    relative paths resolve against the feasibility directory).

.PARAMETER TaskName
    Optional suffix appended to the fixed prefix 'Veles-OTP23-PoC'; safe characters only
    (A-Z a-z 0-9 _ -).

.PARAMETER DiagnosticDelaySeconds
    Optional positive pause for short idle-failure diagnoses, passed to the driver as
    --diagnostic-delay-seconds (0-3600). Only valid with -Script poc_background.py; 0 (the
    default) passes nothing and keeps the default real 10-minute behaviour unchanged.
#>
[CmdletBinding()]
param(
    [switch]$Schedule,
    [ValidateSet('physical_validate.py', 'poc_background.py', 'strict_background.py')][string]$Script = 'physical_validate.py',
    [ValidateNotNullOrEmpty()][string]$OutPath = 'evidence/poc-physical.json',
    [string]$TaskName = '',
    [ValidateRange(0, 3600)][int]$DiagnosticDelaySeconds = 0,
    [ValidateSet('', 'cold', 'prewarm')][string]$Scenario = '',
    [string]$AdbPath = '',
    [string]$PhoneSerials = '',
    [Nullable[double]]$PhoneClockOffsetMs = $null,
    [Nullable[double]]$PhoneClockUncertaintyMs = $null,
    [string]$PhoneClockSource = '',
    [ValidateRange(0, 3600)][int]$ForegroundWaitSeconds = 0
)

$ErrorActionPreference = 'Stop'
if ($DiagnosticDelaySeconds -and $Script -ne 'poc_background.py') {
    throw '-DiagnosticDelaySeconds is only valid when -Script poc_background.py'
}
$strict = $Script -eq 'strict_background.py'
if ($strict -and -not $Scenario) { throw '-Scenario cold|prewarm is required with -Script strict_background.py' }
if (-not $strict -and ($Scenario -or $AdbPath -or $PhoneSerials)) {
    throw '-Scenario, -AdbPath and -PhoneSerials are only valid with -Script strict_background.py'
}
if (-not $strict -and $ForegroundWaitSeconds) { throw '-ForegroundWaitSeconds is only valid with -Script strict_background.py' }
if ($strict -and $DiagnosticDelaySeconds) { throw '-DiagnosticDelaySeconds is not valid with strict_background.py' }
if ($PhoneClockSource -and $PhoneClockSource -notmatch '^[A-Za-z0-9 ._:,+/=-]{1,200}$') { throw '-PhoneClockSource has unsupported characters' }
if (-not $strict -and ($null -ne $PhoneClockOffsetMs -or $null -ne $PhoneClockUncertaintyMs -or $PhoneClockSource)) {
    throw '-PhoneClock* parameters are only valid with -Script strict_background.py'
}
if ($AdbPath -and $AdbPath -match '["\r\n]') { throw '-AdbPath must not contain quotes or line breaks' }
$serialList = @()
if ($PhoneSerials) {
    $serialList = @($PhoneSerials -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    foreach ($serial in $serialList) {
        if ($serial -notmatch '^[A-Za-z0-9._:-]{1,64}$') { throw 'a phone serial contains characters outside A-Za-z0-9._:-' }
    }
}
$root = Split-Path -Parent $PSScriptRoot
if ($TaskName -and $TaskName -notmatch '^[A-Za-z0-9_-]{0,64}$') {
    throw "TaskName suffix must match ^[A-Za-z0-9_-]{0,64} (got '$TaskName')"
}
$taskFullName = 'Veles-OTP23-PoC' + $TaskName
$evidencePath = Join-Path $root 'evidence'
New-Item -ItemType Directory -Force -Path $evidencePath | Out-Null
if ($strict -and -not $PSBoundParameters.ContainsKey('OutPath')) { $OutPath = "evidence/strict-$Scenario.json" }
$outAbs = if ([System.IO.Path]::IsPathRooted($OutPath)) { $OutPath } else { Join-Path $root $OutPath }
$statusPath = Join-Path $evidencePath 'poc-run-status.json'
$transcriptPath = Join-Path $evidencePath 'tmp-poc-transcript.txt'

if ($Schedule) {
    $inner = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Script {1} -OutPath "{2}"' -f $PSCommandPath, $Script, $outAbs
    if ($TaskName) { $inner += " -TaskName $TaskName" }
    if ($DiagnosticDelaySeconds) { $inner += " -DiagnosticDelaySeconds $DiagnosticDelaySeconds" }
    if ($strict) {
        $inner += " -Scenario $Scenario"
        if ($ForegroundWaitSeconds) { $inner += " -ForegroundWaitSeconds $ForegroundWaitSeconds" }
        if ($AdbPath) { $inner += ' -AdbPath "{0}"' -f $AdbPath }
        if ($serialList.Count) { $inner += ' -PhoneSerials "{0}"' -f ($serialList -join ',') }
        if ($null -ne $PhoneClockOffsetMs) { $inner += " -PhoneClockOffsetMs $PhoneClockOffsetMs" }
        if ($null -ne $PhoneClockUncertaintyMs) { $inner += " -PhoneClockUncertaintyMs $PhoneClockUncertaintyMs" }
        if ($PhoneClockSource) { $inner += ' -PhoneClockSource "{0}"' -f $PhoneClockSource }
    }
    try {
        $action = New-ScheduledTaskAction -Execute (Join-Path $env:windir 'System32\WindowsPowerShell\v1.0\powershell.exe') -Argument $inner -WorkingDirectory $root
        $principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)
        Register-ScheduledTask -TaskName $taskFullName -Action $action -Principal $principal -Settings $settings -Force | Out-Null
        Remove-Item -LiteralPath $statusPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath "$statusPath.tmp" -Force -ErrorAction SilentlyContinue
        Start-ScheduledTask -TaskName $taskFullName
    } catch {
        [pscustomobject]@{ task_name = $taskFullName; status_file = $statusPath; error = $_.Exception.Message } | ConvertTo-Json
        exit 1
    }
    [pscustomobject]@{ task_name = $taskFullName; status_file = $statusPath; out_path = $outAbs } | ConvertTo-Json
    exit 0
}

$transcriptActive = $false
try {
    Start-Transcript -Path $transcriptPath -Force -ErrorAction Stop | Out-Null
    $transcriptActive = $true
} catch {
    Write-Warning "transcript unavailable: $($_.Exception.Message)"
}

$startedUtc = [DateTime]::UtcNow.ToString('o')
$code = 1
try {
    & (Join-Path $PSScriptRoot 'register-host.ps1') -Flavour plain -ExePath (Join-Path $root 'target\release\otp23-host.exe')
    Push-Location -LiteralPath $root
    try {
        if ($strict) {
            # strict_background.py has no --flavour option; it always uses the registered plain host.
            $pyArgs = @((Join-Path 'scripts' $Script), '--scenario', $Scenario, '--out', $outAbs)
            if ($ForegroundWaitSeconds) { $pyArgs += @('--foreground-wait-s', [string]$ForegroundWaitSeconds) }
            if ($AdbPath) { $pyArgs += @('--adb-path', $AdbPath) }
            foreach ($serial in $serialList) { $pyArgs += @('--adb', $serial) }
            if ($null -ne $PhoneClockOffsetMs) { $pyArgs += @('--phone-clock-offset-ms', [string]$PhoneClockOffsetMs) }
            if ($null -ne $PhoneClockUncertaintyMs) { $pyArgs += @('--phone-clock-uncertainty-ms', [string]$PhoneClockUncertaintyMs) }
            if ($PhoneClockSource) { $pyArgs += @('--phone-clock-source', $PhoneClockSource) }
        } else {
            $pyArgs = @((Join-Path 'scripts' $Script), '--flavour', 'plain', '--out', $outAbs)
        }
        if ($DiagnosticDelaySeconds) { $pyArgs += @('--diagnostic-delay-seconds', $DiagnosticDelaySeconds) }
        & python @pyArgs
        $code = $LASTEXITCODE
        if ($null -eq $code) { $code = 1 }
    } finally {
        Pop-Location
    }
} catch {
    Write-Warning "run failed: $($_.Exception.Message)"
    $code = 1
} finally {
    try { & (Join-Path $PSScriptRoot 'unregister-host.ps1') -Flavour plain } catch { Write-Warning "teardown: $($_.Exception.Message)" }
    $command = "python scripts/$Script --flavour plain --out $outAbs"
    if ($strict) {
        $command = "python scripts/$Script --scenario $Scenario --out $outAbs"
        if ($AdbPath) { $command += ' --adb-path <set>' }
        if ($serialList.Count) { $command += ' --adb <redacted> x' + $serialList.Count }
    }
    if ($DiagnosticDelaySeconds) { $command += " --diagnostic-delay-seconds $DiagnosticDelaySeconds" }
    $status = [ordered]@{
        script                    = $Script
        flavour                   = if ($strict) { $null } else { 'plain' }
        scenario                  = if ($strict) { $Scenario } else { $null }
        phone_serial_count        = $serialList.Count
        diagnostic_delay_seconds  = $DiagnosticDelaySeconds
        task_name                 = $taskFullName
        command                   = $command
        out_path     = $outAbs
        transcript   = $transcriptPath
        started_utc  = $startedUtc
        finished_utc = [DateTime]::UtcNow.ToString('o')
        exit_code    = $code
    }
    $json = $status | ConvertTo-Json -Depth 3
    $tmp = "$statusPath.tmp"
    try {
        [System.IO.File]::WriteAllText($tmp, $json, (New-Object System.Text.UTF8Encoding($false)))
        $moved = $false
        for ($attempt = 0; $attempt -lt 3 -and -not $moved; $attempt++) {
            try { Move-Item -LiteralPath $tmp -Destination $statusPath -Force -ErrorAction Stop; $moved = $true }
            catch { Start-Sleep -Milliseconds 200 }
        }
        if (-not $moved) { [System.IO.File]::WriteAllText($statusPath, $json, (New-Object System.Text.UTF8Encoding($false))) }
        if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force }
    } catch {
        Write-Warning "status write failed: $($_.Exception.Message)"
    }
    if ($transcriptActive) { try { Stop-Transcript | Out-Null } catch { } }
}
exit $code