$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Name = 'VelesOtp23A01M3-20261030'
if (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) { throw 'Do not overwrite existing task' }
if (Test-Path (Join-Path $Root 'capture\a01-m3.json')) { throw 'Do not overwrite existing M3 capture' }
$Action = New-ScheduledTaskAction -Execute (Get-Command python).Source -Argument "`"$Root\a01-m3.py`"" -WorkingDirectory $Root
$Principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $Name -Action $Action -Principal $Principal -Settings (New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5)) | Out-Null
Start-ScheduledTask -TaskName $Name
'Started bounded M3 controller'
