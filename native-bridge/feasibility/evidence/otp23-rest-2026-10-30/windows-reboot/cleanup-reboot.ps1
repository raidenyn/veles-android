$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Capture = Join-Path $Root 'capture'
$Removed = @()
foreach ($Task in @(Get-ScheduledTask -TaskName 'VelesOtp23W10Access-*-20261030' -ErrorAction SilentlyContinue)) {
    if ($Task.Actions.Arguments -notlike "*$Root*") { throw 'Do not remove an unrelated task' }
    if ($Task.State -eq 'Running') { throw 'Owned access task still running' }
    $Removed += $Task.TaskName
    Unregister-ScheduledTask -TaskName $Task.TaskName -Confirm:$false
}
$Key = 'HKCU:\Software\Google\Chrome\NativeMessagingHosts\me.nagaev.veles.otp23.plain'
if (Test-Path $Key) {
    if ((Get-Item $Key).GetValue('') -notlike "$Root\host-registration\*") { throw 'Do not remove an unrelated registration' }
    & (Join-Path $Root 'scripts\unregister-host.ps1') -Flavour plain -InstallRoot (Join-Path $Root 'host-registration')
}
$Properties = @(Get-PnpDeviceProperty -InstanceId 'USB\VID_0489&PID_E10A\8&FAD588D&0&16' -KeyName DEVPKEY_Device_ConfigFlags,DEVPKEY_Device_ProblemCode,DEVPKEY_Device_DevNodeStatus | Select-Object KeyName,Data)
$OwnProcesses = @(Get-CimInstance Win32_Process -Filter "Name='otp23-host.exe'" | Where-Object { $_.ExecutablePath -like "$Root\*" } | Select-Object ProcessId,ExecutablePath)
$HolderAlive = @(Get-Process -Id 54712 -ErrorAction SilentlyContinue | Select-Object Id,ProcessName)
$Record = [ordered]@{ utc=[DateTime]::UtcNow.ToString('o'); registration_absent= -not (Test-Path $Key);
    own_processes_left=$OwnProcesses; holder_process_left=$HolderAlive; tasks_removed=$Removed;
    own_tasks_left=@(Get-ScheduledTask -TaskName 'VelesOtp23W10Access-*-20261030','VelesOtp23W10Restore-20261030' -ErrorAction SilentlyContinue | Select-Object TaskName,State);
    adapter_properties=$Properties; os=[Environment]::OSVersion.VersionString }
[IO.File]::WriteAllText((Join-Path $Capture 'final-reboot-cleanup.json'), ($Record | ConvertTo-Json -Depth 8))
$Record | ConvertTo-Json -Depth 8
& (Join-Path $Root 'w10-state.ps1') -Action Status
& python -c "import pathlib,zipfile; r=pathlib.Path(r'$Root'); z=zipfile.ZipFile(r/'reboot-text-evidence.zip','w',zipfile.ZIP_DEFLATED); [z.write(p,p.relative_to(r)) for p in (r/'capture').iterdir() if p.is_file() and p.suffix in ('.json','.log','.txt')]; [z.write(p,p.name) for p in r.iterdir() if p.is_file() and p.suffix in ('.py','.ps1','.cs')]; z.close(); print('text evidence archive created')"
