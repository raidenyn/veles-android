$ErrorActionPreference = 'Stop'
$Device = 'USB\VID_0489&PID_E10A\8&FAD588D&0&16'
Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class VelesAdapterConfig {
    [StructLayout(LayoutKind.Sequential)]
    struct DeviceInfo { public int Size; public Guid ClassGuid; public uint DevInst; public UIntPtr Reserved; }
    [DllImport("setupapi.dll", SetLastError=true, CharSet=CharSet.Unicode)]
    static extern IntPtr SetupDiGetClassDevsW(IntPtr guid, string enumerator, IntPtr window, uint flags);
    [DllImport("setupapi.dll", SetLastError=true, CharSet=CharSet.Unicode)]
    static extern bool SetupDiOpenDeviceInfoW(IntPtr set, string id, IntPtr window, uint flags, ref DeviceInfo info);
    [DllImport("setupapi.dll", SetLastError=true, CharSet=CharSet.Unicode)]
    static extern bool SetupDiGetDeviceRegistryPropertyW(IntPtr set, ref DeviceInfo info, uint property, out uint type, byte[] buffer, uint length, out uint needed);
    [DllImport("setupapi.dll", SetLastError=true, CharSet=CharSet.Unicode)]
    static extern bool SetupDiSetDeviceRegistryPropertyW(IntPtr set, ref DeviceInfo info, uint property, byte[] buffer, uint length);
    [DllImport("setupapi.dll", SetLastError=true)]
    static extern bool SetupDiDestroyDeviceInfoList(IntPtr set);
    public static uint ClearDisabledFlag(string id) {
        if (id != @"USB\VID_0489&PID_E10A\8&FAD588D&0&16") throw new InvalidOperationException("Unexpected adapter");
        var set = SetupDiGetClassDevsW(IntPtr.Zero, null, IntPtr.Zero, 4);
        if (set == new IntPtr(-1)) throw new Win32Exception();
        try {
            var info = new DeviceInfo { Size=Marshal.SizeOf(typeof(DeviceInfo)) };
            if (!SetupDiOpenDeviceInfoW(set,id,IntPtr.Zero,0,ref info)) throw new Win32Exception();
            var bytes = new byte[4]; uint type, needed;
            if (!SetupDiGetDeviceRegistryPropertyW(set,ref info,10,out type,bytes,4,out needed)) throw new Win32Exception();
            if (type != 4 || needed != 4) throw new InvalidOperationException("Unexpected CONFIGFLAGS shape");
            uint before = BitConverter.ToUInt32(bytes,0);
            var enabled = BitConverter.GetBytes(before & ~1u);
            if (!SetupDiSetDeviceRegistryPropertyW(set,ref info,10,enabled,4)) throw new Win32Exception();
            return before;
        } finally { SetupDiDestroyDeviceInfoList(set); }
    }
}
'@
$Before = [VelesAdapterConfig]::ClearDisabledFlag($Device)
$Properties = @(Get-PnpDeviceProperty -InstanceId $Device -KeyName DEVPKEY_Device_ConfigFlags,DEVPKEY_Device_ProblemCode,DEVPKEY_Device_DevNodeStatus | Select-Object KeyName,Data)
$Flag = ($Properties | Where-Object KeyName -eq 'DEVPKEY_Device_ConfigFlags').Data
if (($Flag -band 1) -ne 0) { throw 'Adapter disabled flag still present' }
$Json = [ordered]@{ utc=[DateTime]::UtcNow.ToString('o'); operation='Clear only CONFIGFLAG_DISABLED on approved Bluetooth adapter via SetupAPI';
    before_config_flags=$Before; after=$Properties; device=(Get-PnpDevice -InstanceId $Device | Select-Object Status,FriendlyName) } | ConvertTo-Json -Depth 8
[IO.File]::WriteAllText((Join-Path $PSScriptRoot ('capture\restored-persistent-enabled-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fff') + '.json')), $Json)
$Json
