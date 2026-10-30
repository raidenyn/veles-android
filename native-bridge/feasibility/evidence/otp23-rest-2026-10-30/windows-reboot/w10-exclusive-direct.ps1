$ErrorActionPreference = 'Stop'
$Capture = Join-Path $PSScriptRoot 'capture'
$Record = [ordered]@{ pid=$PID; started_utc=[DateTime]::UtcNow.ToString('o');
    scope='Exclusive access to only the synthetic Veles GATT service; not a Windows privacy-setting denial';
    release_method='Dedicated process exit releases the held SDK objects; verified separately by process exit and successful native reconnect' }
function Save-Record { [IO.File]::WriteAllText((Join-Path $Capture 'exclusive-direct.json'), ($Record | ConvertTo-Json -Depth 8)) }
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Devices.Bluetooth.BluetoothLEDevice,Windows.Devices.Bluetooth,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceService,Windows.Devices.Bluetooth,ContentType=WindowsRuntime] | Out-Null
. (Join-Path $PSScriptRoot 'w10-vector-helper.ps1')
$AsTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
function Await($Operation,[Type]$ResultType) {
    $Task = $AsTask.MakeGenericMethod($ResultType).Invoke($null,@($Operation))
    if (-not $Task.Wait(15000)) { throw 'WinRT lookup timed out' }
    $Task.Result
}
try {
    $Hint = (Get-Content -Raw (Join-Path $Capture 'w10-access-target.private.json') | ConvertFrom-Json).platform_hint
    if ($Hint -notmatch '^winrt:([0-9a-f]{12}):([012])$') { throw 'Invalid own-phone hint' }
    $Address = [Convert]::ToUInt64($Matches[1],16)
    $AddressType = [Windows.Devices.Bluetooth.BluetoothAddressType]([int]$Matches[2])
    $Device = Await ([Windows.Devices.Bluetooth.BluetoothLEDevice]::FromBluetoothAddressAsync($Address,$AddressType)) ([Windows.Devices.Bluetooth.BluetoothLEDevice])
    $Result = Await ($Device.GetGattServicesForUuidAsync([Guid]'8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51',[Windows.Devices.Bluetooth.BluetoothCacheMode]::Uncached)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceServicesResult])
    $Record.services_status = $Result.Status.ToString()
    $Summary = [VelesGattVector]::HoldAndSubscribe($Result)
    $Parts = $Summary.Split('|')
    $Record.open_status = $Parts[0]
    $Record.sharing_mode = $Parts[1]
    $Record.subscription_status = $Parts[2]
    $Record.status = 'HELD'
    $Record.held_utc = [DateTime]::UtcNow.ToString('o')
    Save-Record
    $Deadline = (Get-Date).AddSeconds(180)
    while (-not (Test-Path (Join-Path $Capture 'exclusive-direct-release'))) {
        if ((Get-Date) -gt $Deadline) { throw 'Bounded hold deadline expired' }
        Start-Sleep -Milliseconds 200
    }
    $Record.status = 'RELEASING_BY_PROCESS_EXIT'
} catch {
    $Record.status = 'ERROR'
    $Record.error = $_.Exception.Message
} finally {
    $Record.finished_utc = [DateTime]::UtcNow.ToString('o')
    Save-Record
}
