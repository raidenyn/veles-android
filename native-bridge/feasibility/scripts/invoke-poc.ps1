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
    Python driver filename under scripts\ (allowlist: physical_validate.py, poc_background.py).

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
    [ValidateSet('physical_validate.py', 'poc_background.py')][string]$Script = 'physical_validate.py',
    [ValidateNotNullOrEmpty()][string]$OutPath = 'evidence/poc-physical.json',
    [string]$TaskName = '',
    [ValidateRange(0, 3600)][int]$DiagnosticDelaySeconds = 0
)

$ErrorActionPreference = 'Stop'
if ($DiagnosticDelaySeconds -and $Script -ne 'poc_background.py') {
    throw '-DiagnosticDelaySeconds is only valid when -Script poc_background.py'
}
$root = Split-Path -Parent $PSScriptRoot
if ($TaskName -and $TaskName -notmatch '^[A-Za-z0-9_-]{0,64}$') {
    throw "TaskName suffix must match ^[A-Za-z0-9_-]{0,64} (got '$TaskName')"
}
$taskFullName = 'Veles-OTP23-PoC' + $TaskName
$evidencePath = Join-Path $root 'evidence'
New-Item -ItemType Directory -Force -Path $evidencePath | Out-Null
$outAbs = if ([System.IO.Path]::IsPathRooted($OutPath)) { $OutPath } else { Join-Path $root $OutPath }
$statusPath = Join-Path $evidencePath 'poc-run-status.json'
$transcriptPath = Join-Path $evidencePath 'tmp-poc-transcript.txt'

if ($Schedule) {
    $inner = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Script {1} -OutPath "{2}"' -f $PSCommandPath, $Script, $outAbs
    if ($TaskName) { $inner += " -TaskName $TaskName" }
    if ($DiagnosticDelaySeconds) { $inner += " -DiagnosticDelaySeconds $DiagnosticDelaySeconds" }
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
        $pyArgs = @((Join-Path 'scripts' $Script), '--flavour', 'plain', '--out', $outAbs)
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
    if ($DiagnosticDelaySeconds) { $command += " --diagnostic-delay-seconds $DiagnosticDelaySeconds" }
    $status = [ordered]@{
        script                    = $Script
        flavour                   = 'plain'
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