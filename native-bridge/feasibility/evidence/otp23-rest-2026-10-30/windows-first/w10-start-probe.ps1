param([Parameter(Mandatory)][ValidateSet('baseline','radio-off','adapter-absent','adapter-absent-v2','restored')][string]$Phase)
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$ExistingOutput = Join-Path $Root "capture\$Phase.json"
if (Test-Path $ExistingOutput) { throw 'Do not overwrite a prior capture' }
$Name = 'VelesOtp23W10Probe-' + $Phase + '-20261030'
if (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) { throw 'Do not overwrite existing task' }
$Python = (Get-Command python).Source
$Principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$Action = New-ScheduledTaskAction -Execute $Python -Argument "`"$Root\w10-probe.py`" --phase $Phase" -WorkingDirectory $Root
$Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 3)
try {
    Register-ScheduledTask -TaskName $Name -Action $Action -Principal $Principal -Settings $Settings | Out-Null
    Start-ScheduledTask -TaskName $Name
    $Out = Join-Path $Root "capture\$Phase.json"
    $Deadline = (Get-Date).AddMinutes(3)
    while (-not (Test-Path $Out)) {
        if ((Get-Date) -gt $Deadline) { throw 'Probe deadline expired; preserve failure and restore Bluetooth' }
        Start-Sleep -Seconds 1
    }
    Get-Content -Raw $Out
} finally {
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
}
