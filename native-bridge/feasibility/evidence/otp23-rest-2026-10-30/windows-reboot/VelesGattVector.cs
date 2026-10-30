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