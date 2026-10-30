$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Capture = Join-Path $Root 'capture'
$Name = 'VelesOtp23A01M3-20261030'
$Task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
if ($Task) {
    if ($Task.Actions.Arguments -notlike "*$Root*a01-m3.py*") { throw 'Unexpected task action; do not remove' }
    if ($Task.State -eq 'Running') { throw 'M3 controller is still running' }
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false
}
$Key = 'HKCU:\Software\Google\Chrome\NativeMessagingHosts\me.nagaev.veles.otp23.plain'
if (Test-Path $Key) {
    $Manifest = (Get-Item $Key).GetValue('')
    if ($Manifest -notlike "$Root\host-registration\*") { throw 'Unexpected registration; do not remove' }
    & (Join-Path $Root 'scripts\unregister-host.ps1') -Flavour plain -InstallRoot (Join-Path $Root 'host-registration')
}
$Properties = @(Get-PnpDeviceProperty -InstanceId 'USB\VID_0489&PID_E10A\8&FAD588D&0&16' -KeyName DEVPKEY_Device_ConfigFlags,DEVPKEY_Device_ProblemCode,DEVPKEY_Device_DevNodeStatus | Select-Object KeyName,Data)
$OwnProcesses = @(Get-CimInstance Win32_Process -Filter "Name='otp23-host.exe'" | Where-Object { $_.ExecutablePath -like "$Root\*" } | Select-Object ProcessId,ExecutablePath)
$TasksLeft = @(Get-ScheduledTask -TaskName 'VelesOtp23A01M3-20261030','VelesOtp23W10Restore-20261030' -ErrorAction SilentlyContinue | Select-Object TaskName,State)
$Record = [ordered]@{ utc=[DateTime]::UtcNow.ToString('o'); registration_absent= -not (Test-Path $Key);
    own_processes_left=$OwnProcesses; own_tasks_left=$TasksLeft; adapter_properties=$Properties }
$Json = $Record | ConvertTo-Json -Depth 6
[IO.File]::WriteAllText((Join-Path $Capture 'final-pc-cleanup.json'), $Json)
$Json
& (Join-Path $Root 'w10-state.ps1') -Action Status
& python -c "import pathlib,zipfile; r=pathlib.Path(r'$Root'); z=zipfile.ZipFile(r/'rest-text-evidence.zip','w',zipfile.ZIP_DEFLATED); [z.write(p,p.relative_to(r)) for p in (r/'capture').iterdir() if p.is_file() and p.suffix in ('.json','.log','.txt')]; [z.write(p,p.name) for p in r.iterdir() if p.is_file() and p.suffix in ('.py','.ps1')]; z.close(); print('text evidence archive created')"
