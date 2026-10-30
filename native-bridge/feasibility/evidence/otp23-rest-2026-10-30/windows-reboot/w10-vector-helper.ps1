$ErrorActionPreference = 'Stop'
$Runtime = [System.Runtime.InteropServices.RuntimeEnvironment]::GetRuntimeDirectory()
$References = @(
    'C:\Program Files (x86)\Windows Kits\10\UnionMetadata\10.0.26100.0\Windows.winmd',
    (Join-Path $Runtime 'System.Runtime.WindowsRuntime.dll'),
    'C:\Program Files (x86)\Reference Assemblies\Microsoft\Framework\.NETFramework\v4.7.2\Facades\System.Runtime.dll',
    (Join-Path $Runtime 'System.dll'),
    (Join-Path $Runtime 'System.Core.dll')
)
$Source = @'
using System;
using Windows.Devices.Bluetooth;
using Windows.Devices.Bluetooth.GenericAttributeProfile;
public static class VelesGattVector {
    static GattDeviceService held;
    public static string HoldAndSubscribe(object value) {
        var result = (GattDeviceServicesResult)value;
        if (result.Status != GattCommunicationStatus.Success || result.Services.Count != 1)
            throw new InvalidOperationException("Expected one successful own service");
        held = result.Services[0];
        if (held.Uuid != new Guid("8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51"))
            throw new InvalidOperationException("Unexpected service UUID");
        var opened = held.OpenAsync(GattSharingMode.Exclusive).AsTask().GetAwaiter().GetResult();
        if (held.SharingMode != GattSharingMode.Exclusive)
            throw new InvalidOperationException("Open status=" + opened + "; sharing mode=" + held.SharingMode);
        var chars = held.GetCharacteristicsForUuidAsync(new Guid("8e400002-8f3c-4d1a-b64a-8e3c9b0d2a51"),BluetoothCacheMode.Uncached).AsTask().GetAwaiter().GetResult();
        if (chars.Status != GattCommunicationStatus.Success || chars.Characteristics.Count != 1)
            throw new InvalidOperationException("Notify lookup failed");
        var status = chars.Characteristics[0].WriteClientCharacteristicConfigurationDescriptorAsync(GattClientCharacteristicConfigurationDescriptorValue.Notify).AsTask().GetAwaiter().GetResult();
        if (status != GattCommunicationStatus.Success) throw new InvalidOperationException("Subscription failed: " + status);
        return opened + "|" + held.SharingMode + "|" + status;
    }
    public static object FirstService(object value) {
        var result = (GattDeviceServicesResult)value;
        if (result.Status != GattCommunicationStatus.Success || result.Services.Count != 1)
            throw new InvalidOperationException("Expected one successful own service");
        return result.Services[0];
    }
    public static object FirstCharacteristic(object value) {
        var result = (GattCharacteristicsResult)value;
        if (result.Status != GattCommunicationStatus.Success || result.Characteristics.Count != 1)
            throw new InvalidOperationException("Expected one successful notify characteristic");
        return result.Characteristics[0];
    }
}
'@
$SourcePath = Join-Path $PSScriptRoot 'VelesGattVector.cs'
$Library = Join-Path $PSScriptRoot 'VelesGattVector.dll'
[IO.File]::WriteAllText($SourcePath, $Source)
$CompilerOutput = & (Join-Path $Runtime 'csc.exe') /nologo /target:library "/out:$Library" (('/reference:' + ($References -join ';'))) $SourcePath 2>&1
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'capture\vector-compiler.txt'), ($CompilerOutput -join "`n"))
if ($LASTEXITCODE -ne 0) { throw ($CompilerOutput -join "`n") }
Add-Type -Path $Library
