$ErrorActionPreference = 'Stop'
$Capture = Join-Path $PSScriptRoot 'capture'
$Record = [ordered]@{ started_utc=[DateTime]::UtcNow.ToString('o'); scope='Exclusive access to only the synthetic Veles GATT service on the one test phone; not a Windows privacy-setting denial' }
$Device = $Service = $Exclusive = $null
function Save-Record { [IO.File]::WriteAllText((Join-Path $Capture 'exclusive-holder.json'), ($Record | ConvertTo-Json -Depth 8)) }
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Devices.Bluetooth.BluetoothLEDevice,Windows.Devices.Bluetooth,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceService,Windows.Devices.Bluetooth,ContentType=WindowsRuntime] | Out-Null
$AsTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
function Await($Operation,[Type]$ResultType) {
    $Task = $AsTask.MakeGenericMethod($ResultType).Invoke($null,@($Operation))
    if (-not $Task.Wait(15000)) { throw 'WinRT holder operation timed out' }
    $Task.Result
}
function Close-Object($Value) {
    if ($null -ne $Value) {
        if ($Value -is [IDisposable]) { $Value.Dispose() } else { $Value.Close() }
    }
}
try {
    $Hint = (Get-Content -Raw (Join-Path $Capture 'w10-access-target.private.json') | ConvertFrom-Json).platform_hint
    if ($Hint -notmatch '^winrt:([0-9a-f]{12}):([012])$') { throw 'Invalid own-phone hint' }
    $Address = [Convert]::ToUInt64($Matches[1],16)
    $AddressType = [Windows.Devices.Bluetooth.BluetoothAddressType]([int]$Matches[2])
    $Device = Await ([Windows.Devices.Bluetooth.BluetoothLEDevice]::FromBluetoothAddressAsync($Address,$AddressType)) ([Windows.Devices.Bluetooth.BluetoothLEDevice])
    if (-not $Device) { throw 'No test peripheral' }
    $Uuid = [Guid]'8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51'
    $Result = Await ($Device.GetGattServicesForUuidAsync($Uuid,[Windows.Devices.Bluetooth.BluetoothCacheMode]::Uncached)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceServicesResult])
    $Record.services_status = $Result.Status.ToString()
    if ($Result.Status.ToString() -ne 'Success' -or $Result.Services.Count -ne 1) { throw 'Own service lookup failed' }
    $Service = $Result.Services[0]
    $Id = $Service.DeviceId
    Close-Object $Service
    $Service = $null
    $Exclusive = Await ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceService]::FromIdAsync($Id,[Windows.Devices.Bluetooth.GenericAttributeProfile.GattSharingMode]::Exclusive)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceService])
    if (-not $Exclusive) { throw 'Exclusive service open returned null' }
    $Record.sharing_mode = $Exclusive.SharingMode.ToString()
    if ($Record.sharing_mode -ne 'Exclusive') { throw 'Exclusive ownership not established' }
    $Chars = Await ($Exclusive.GetCharacteristicsForUuidAsync([Guid]'8e400002-8f3c-4d1a-b64a-8e3c9b0d2a51',[Windows.Devices.Bluetooth.BluetoothCacheMode]::Uncached)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattCharacteristicsResult])
    if ($Chars.Status.ToString() -ne 'Success' -or $Chars.Characteristics.Count -ne 1) { throw 'Notify characteristic lookup failed' }
    $Status = Await ($Chars.Characteristics[0].WriteClientCharacteristicConfigurationDescriptorAsync([Windows.Devices.Bluetooth.GenericAttributeProfile.GattClientCharacteristicConfigurationDescriptorValue]::Notify)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattCommunicationStatus])
    $Record.subscription_status = $Status.ToString()
    if ($Record.subscription_status -ne 'Success') { throw 'Holder subscription failed' }
    $Record.status = 'HELD'
    $Record.held_utc = [DateTime]::UtcNow.ToString('o')
    Save-Record
    $Deadline = (Get-Date).AddSeconds(180)
    while (-not (Test-Path (Join-Path $Capture 'exclusive-release'))) {
        if ((Get-Date) -gt $Deadline) { throw 'Bounded exclusive hold deadline expired' }
        Start-Sleep -Milliseconds 200
    }
    $Record.status = 'RELEASED'
} catch {
    $Record.status = 'ERROR'
    $Record.error = $_.Exception.Message
} finally {
    $CloseErrors = @()
    foreach ($Value in @($Exclusive,$Service,$Device)) { try { Close-Object $Value } catch { $CloseErrors += $_.Exception.Message } }
    $Record.close_errors = $CloseErrors
    $Record.finished_utc = [DateTime]::UtcNow.ToString('o')
    Save-Record
}
