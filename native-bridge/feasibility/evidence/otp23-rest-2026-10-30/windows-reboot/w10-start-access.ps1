param([Parameter(Mandatory)][ValidateSet('baseline','baseline-v2','held','recovered','holder','holder-retake1','identify','holder-process','identify-v2','holder-process-v2','identify-v3','holder-process-v3','identify-v4','holder-process-v4','holder-direct')][string]$Phase)
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Name = 'VelesOtp23W10Access-' + $Phase + '-20261030'
if (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) { throw 'Do not overwrite an existing task' }
$Principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
if ($Phase -like 'holder*' -or $Phase -like 'identify*') {
    $FileName = if ($Phase -eq 'holder-direct') { 'w10-exclusive-direct.ps1' } elseif ($Phase -eq 'holder') { 'w10-exclusive-holder.ps1' } elseif ($Phase -eq 'holder-retake1') { 'w10-exclusive-holder-retake1.ps1' } elseif ($Phase -like '*-v4') { 'w10-exclusive-process-v4.ps1' } elseif ($Phase -like '*-v3') { 'w10-exclusive-process-v3.ps1' } elseif ($Phase -like '*-v2') { 'w10-exclusive-process-v2.ps1' } else { 'w10-exclusive-process.ps1' }
    $Extra = if ($Phase -like 'identify*') { ' -Identify' } else { '' }
    $Action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Root\$FileName`"$Extra" -WorkingDirectory $Root
} else {
    $Action = New-ScheduledTaskAction -Execute (Get-Command python).Source -Argument "`"$Root\w10-access-probe.py`" --phase $Phase" -WorkingDirectory $Root
}
Register-ScheduledTask -TaskName $Name -Action $Action -Principal $Principal -Settings (New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 4)) | Out-Null
Start-ScheduledTask -TaskName $Name
if ($Phase -notlike 'holder*' -and $Phase -notlike 'identify*') {
    $File = Join-Path $Root "capture\access-$Phase.json"
    $Deadline = (Get-Date).AddMinutes(3)
    while (-not (Test-Path $File)) {
        if ((Get-Date) -gt $Deadline) { throw 'Access probe deadline expired' }
        Start-Sleep -Seconds 1
    }
    Get-Content -Raw $File
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false
} else { 'Started bounded exclusive holder' }
