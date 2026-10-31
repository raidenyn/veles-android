param([Parameter(Mandatory)][ValidateSet('Status','Arm','RadioOff','DisableAdapter','Restore','Disarm')][string]$Action)
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Capture = Join-Path $Root 'capture'
New-Item -ItemType Directory -Force $Capture | Out-Null
$Device = 'USB\VID_0489&PID_E10A\8&FAD588D&0&16'
$TaskName = 'VelesOtp23W10Restore-20261030'
$Receipt = Join-Path $Capture 'restore-receipt.json'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
$AsTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
function Await($Operation, [Type]$ResultType) {
    $Task = $AsTask.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    if (-not $Task.Wait(20000)) { throw 'WinRT operation exceeded 20 seconds' }
    $Task.Result
}
function Radio {
    $All = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
    $Found = @($All | Where-Object { $_.Kind.ToString() -eq 'Bluetooth' })
    if ($Found.Count -ne 1) { throw "Expected exactly one Bluetooth radio, saw $($Found.Count)" }
    $Found[0]
}
function Read-State {
    $Pnp = Get-PnpDevice -InstanceId $Device
    $RadioState = $null
    $RadioError = $null
    try { $RadioState = (Radio).State.ToString() } catch { $RadioError = $_.Exception.Message }
    [ordered]@{ utc=[DateTime]::UtcNow.ToString('o'); adapter=$Pnp.FriendlyName;
        instance_id=$Pnp.InstanceId; adapter_status=$Pnp.Status; radio_state=$RadioState; radio_error=$RadioError }
}
function Set-Radio([string]$Value) {
    $Radio = Radio
    $Desired = [Windows.Devices.Radios.RadioState]::$Value
    $Access = Await ($Radio.SetStateAsync($Desired)) ([Windows.Devices.Radios.RadioAccessStatus])
    if ($Access.ToString() -ne 'Allowed') { throw "Radio state change denied: $Access" }
    Start-Sleep -Seconds 2
    if ((Radio).State.ToString() -ne $Value) { throw "Radio did not reach $Value" }
}
function Renew-Watchdog {
    $Task = Get-ScheduledTask -TaskName $TaskName
    if (-not $Task.Settings.Enabled -or $Task.Actions.Arguments -notlike "*$PSCommandPath*Restore*") {
        throw 'Verified own restoration task is not armed'
    }
    Set-ScheduledTask -TaskName $TaskName -Trigger (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5)) | Out-Null
}
$Before = Read-State
switch ($Action) {
    'Arm' {
        if ($Before.adapter_status -ne 'OK' -or $Before.radio_state -ne 'On') { throw 'Baseline is not adapter OK / radio On' }
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { throw 'Restoration task already exists; do not overwrite' }
        $Principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Highest
        $TaskAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Action Restore"
        $Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -StartWhenAvailable
        Register-ScheduledTask -TaskName $TaskName -Action $TaskAction -Principal $Principal -Settings $Settings -Trigger (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5)) | Out-Null
        Start-ScheduledTask -TaskName $TaskName
        $Deadline = (Get-Date).AddSeconds(45)
        while (-not (Test-Path $Receipt)) {
            if ((Get-Date) -gt $Deadline) { throw 'Restoration dry run did not complete; no radio/adapter change authorized' }
            Start-Sleep -Seconds 1
        }
        $Proof = Get-Content -Raw $Receipt | ConvertFrom-Json
        if ($Proof.after.adapter_status -ne 'OK' -or $Proof.after.radio_state -ne 'On') { throw 'Restoration dry run failed' }
        Copy-Item $Receipt (Join-Path $Capture 'restore-dry-run.json')
        Remove-Item $Receipt
        Renew-Watchdog
    }
    'RadioOff' { Renew-Watchdog; Set-Radio 'Off' }
    'DisableAdapter' {
        Renew-Watchdog
        $Output = & pnputil.exe /disable-device $Device 2>&1
        $Code = $LASTEXITCODE
        [IO.File]::WriteAllText((Join-Path $Capture 'pnputil-disable.txt'), ($Output -join "`n"))
        if ($Code -ne 0) { throw "Exact-device disable failed with exit $Code; no absent-adapter probe" }
        Start-Sleep -Seconds 3
        $ErrorCode = (Get-CimInstance Win32_PnPEntity | Where-Object { $_.PNPDeviceID -eq $Device }).ConfigManagerErrorCode
        if ($ErrorCode -ne 22) { throw "Adapter not verified disabled: ConfigManagerErrorCode=$ErrorCode" }
    }
    'Restore' {
        if ($Before.adapter_status -ne 'OK') {
            $Output = & pnputil.exe /enable-device $Device 2>&1
            $Code = $LASTEXITCODE
            [IO.File]::WriteAllText((Join-Path $Capture 'pnputil-enable.txt'), ($Output -join "`n"))
            if ($Code -ne 0) { throw "Exact-device enable failed with exit $Code" }
            Start-Sleep -Seconds 5
        }
        Set-Radio 'On'
    }
    'Disarm' {
        if ($Before.adapter_status -ne 'OK' -or $Before.radio_state -ne 'On') { throw 'Restore Bluetooth before removing watchdog' }
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
}
$Record = [ordered]@{ action=$Action; before=$Before; after=(Read-State) }
$Json = $Record | ConvertTo-Json -Depth 8
[IO.File]::WriteAllText((Join-Path $Capture ("state-$Action-" + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fff') + '.json')), $Json)
if ($Action -eq 'Restore') { [IO.File]::WriteAllText($Receipt, $Json) }
$Json
