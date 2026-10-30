@file:Suppress("MissingPermission")

package me.nagaev.veles.sharing

import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothGattServer
import android.bluetooth.BluetoothGattServerCallback
import android.bluetooth.BluetoothGattService
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.BluetoothStatusCodes
import android.bluetooth.le.AdvertiseCallback
import android.bluetooth.le.AdvertiseData
import android.bluetooth.le.AdvertiseSettings
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.ParcelUuid
import android.os.SystemClock
import me.nagaev.veles.BuildConfig
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

/** All platform operations are confined to the main looper. Callbacks are generation-local. */
internal class SharingGattServer(
    private val context: Context,
    private val onState: (SharingAvailability) -> Unit,
    private val onRecord: (String, ByteArray) -> ByteArray?,
) {
    private val handler = Handler(Looper.getMainLooper())
    private val transport = GattTransport(
        if (BuildConfig.OTP23_POC) 512 else 514,
        SystemClock::elapsedRealtime,
    )
    private val ingress = IngressBudget()
    private val admitted = ConcurrentHashMap.newKeySet<String>()
    private val devices = mutableMapOf<String, BluetoothDevice>()
    private val subscribed = mutableSetOf<String>()
    private val connectedAt = mutableMapOf<String, Long>()
    private val ready = mutableSetOf<String>()
    private var server: BluetoothGattServer? = null
    private var closed = false
    private val manager = context.getSystemService(BluetoothManager::class.java)
    private val notify = BluetoothGattCharacteristic(
        NOTIFY_UUID,
        BluetoothGattCharacteristic.PROPERTY_NOTIFY,
        0,
    ).apply {
        addDescriptor(
            BluetoothGattDescriptor(
                CCCD_UUID,
                BluetoothGattDescriptor.PERMISSION_READ or BluetoothGattDescriptor.PERMISSION_WRITE,
            ),
        )
    }
    private val advertiser get() = manager.adapter?.bluetoothLeAdvertiser
    private val advertising = object : AdvertiseCallback() {
        override fun onStartSuccess(settingsInEffect: AdvertiseSettings) = post {
            onState(SharingAvailability.Advertising)
        }
        override fun onStartFailure(errorCode: Int) = post { onState(SharingAvailability.Failed) }
    }
    private val callbacks = object : BluetoothGattServerCallback() {
        override fun onServiceAdded(status: Int, service: BluetoothGattService) = post {
            if (status != BluetoothGatt.GATT_SUCCESS) {
                onState(SharingAvailability.Failed)
            } else {
                startAdvertising()
            }
        }
        override fun onConnectionStateChange(device: BluetoothDevice, status: Int, newState: Int) = post {
            val id = device.address
            if (status != BluetoothGatt.GATT_SUCCESS || newState == BluetoothProfile.STATE_DISCONNECTED) {
                remove(id)
            } else if (newState == BluetoothProfile.STATE_CONNECTED) {
                if (!transport.connect(id)) {
                    server?.cancelConnection(device)
                } else {
                    devices[id] = device
                    admitted.add(id)
                    connectedAt[id] = SystemClock.elapsedRealtime()
                }
            }
        }
        override fun onMtuChanged(device: BluetoothDevice, mtu: Int) = post {
            if (!transport.setMtu(device.address, mtu)) {
                disconnect(device.address)
            } else {
                ready.add(device.address)
            }
        }
        override fun onCharacteristicWriteRequest(
            device: BluetoothDevice,
            requestId: Int,
            characteristic: BluetoothGattCharacteristic,
            preparedWrite: Boolean,
            responseNeeded: Boolean,
            offset: Int,
            value: ByteArray,
        ) {
            // Reject oversized values before copying or posting untrusted data to the main queue.
            if (value.size > 514) return
            postValue(device, value) { copy ->
                val id = device.address
                val validOperation = characteristic.uuid == WRITE_UUID && !preparedWrite && !responseNeeded && offset == 0
                if (!validOperation || id !in ready || id !in subscribed) {
                    if (responseNeeded) server?.sendResponse(device, requestId, BluetoothGatt.GATT_REQUEST_NOT_SUPPORTED, 0, null)
                } else {
                    val result = transport.receive(id, copy)
                    if (result.disconnect) disconnect(id)
                    result.record?.let { record ->
                        val response = onRecord(id, record)
                        record.fill(0)
                        if (response != null) {
                            transport.enqueue(id, response)
                            response.fill(0)
                            pump(id)
                        }
                    }
                }
            }
        }
        override fun onCharacteristicReadRequest(
            device: BluetoothDevice,
            requestId: Int,
            offset: Int,
            characteristic: BluetoothGattCharacteristic,
        ) = post {
            server?.sendResponse(device, requestId, BluetoothGatt.GATT_READ_NOT_PERMITTED, offset, null)
            Unit
        }

        override fun onDescriptorReadRequest(
            device: BluetoothDevice,
            requestId: Int,
            offset: Int,
            descriptor: BluetoothGattDescriptor,
        ) = post {
            val valid = descriptor.uuid == CCCD_UUID && offset == 0
            server?.sendResponse(
                device,
                requestId,
                if (valid) BluetoothGatt.GATT_SUCCESS else BluetoothGatt.GATT_INVALID_OFFSET,
                0,
                if (valid && device.address in subscribed) {
                    BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
                } else {
                    BluetoothGattDescriptor.DISABLE_NOTIFICATION_VALUE
                },
            )
        }
        override fun onDescriptorWriteRequest(
            device: BluetoothDevice,
            requestId: Int,
            descriptor: BluetoothGattDescriptor,
            preparedWrite: Boolean,
            responseNeeded: Boolean,
            offset: Int,
            value: ByteArray,
        ) {
            if (value.size > 2) return
            postValue(device, value) { copy ->
                val valid = descriptor.uuid == CCCD_UUID &&
                    descriptor.characteristic.uuid == NOTIFY_UUID &&
                    !preparedWrite &&
                    offset == 0 &&
                    device.address in ready &&
                    (
                        copy.contentEquals(BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE) ||
                            copy.contentEquals(BluetoothGattDescriptor.DISABLE_NOTIFICATION_VALUE)
                        )
                if (valid) {
                    if (copy.contentEquals(BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE)) {
                        subscribed.add(device.address)
                    } else {
                        disconnect(device.address)
                    }
                }
                if (responseNeeded) {
                    server?.sendResponse(
                        device,
                        requestId,
                        if (valid) BluetoothGatt.GATT_SUCCESS else BluetoothGatt.GATT_REQUEST_NOT_SUPPORTED,
                        0,
                        null,
                    )
                }
            }
        }
        override fun onExecuteWrite(device: BluetoothDevice, requestId: Int, execute: Boolean) = post {
            server?.sendResponse(device, requestId, BluetoothGatt.GATT_REQUEST_NOT_SUPPORTED, 0, null)
            Unit
        }
        override fun onNotificationSent(device: BluetoothDevice, status: Int) = post {
            if (status != BluetoothGatt.GATT_SUCCESS) {
                disconnect(device.address)
            } else {
                transport.notificationComplete(device.address, true)
                pump(device.address)
            }
        }
    }
    private val sweep = object : Runnable {
        override fun run() {
            post {
                transport.expire().forEach(::disconnect)
                connectedAt.filter { (id, at) ->
                    (id !in ready || id !in subscribed) && SystemClock.elapsedRealtime() - at >= 5000
                }
                    .keys.toList().forEach(::disconnect)
                handler.postDelayed(this, 1000)
            }
        }
    }

    fun start() {
        check(Looper.myLooper() == Looper.getMainLooper())
        server = manager.openGattServer(context, callbacks)
        val service = BluetoothGattService(SERVICE_UUID, BluetoothGattService.SERVICE_TYPE_PRIMARY).apply {
            addCharacteristic(
                BluetoothGattCharacteristic(
                    WRITE_UUID,
                    BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE,
                    BluetoothGattCharacteristic.PERMISSION_WRITE,
                ),
            )
            addCharacteristic(notify)
        }
        if (server?.addService(service) != true) onState(SharingAvailability.Failed)
        handler.postDelayed(sweep, 1000)
    }

    private fun startAdvertising() {
        val advertising = advertiser
        if (advertising == null) {
            onState(SharingAvailability.Unsupported)
        } else {
            advertising.startAdvertising(
                AdvertiseSettings.Builder().setConnectable(true).setAdvertiseMode(AdvertiseSettings.ADVERTISE_MODE_LOW_LATENCY).build(),
                AdvertiseData.Builder().addServiceUuid(ParcelUuid(SERVICE_UUID)).build(),
                this.advertising,
            )
        }
    }

    private fun pump(id: String) {
        if (id !in subscribed) return
        val device = devices[id] ?: return
        val bytes = transport.nextFragment(id) ?: return
        if (server?.notifyCharacteristicChanged(device, notify, false, bytes) != BluetoothStatusCodes.SUCCESS) disconnect(id)
    }

    private fun post(block: () -> Unit) {
        handler.post {
            if (!closed) {
                try {
                    block()
                } catch (_: SecurityException) {
                    onState(SharingAvailability.PermissionRequired)
                } catch (_: IllegalStateException) {
                    onState(SharingAvailability.Failed)
                }
            }
        }
    }

    private fun postValue(device: BluetoothDevice, value: ByteArray, block: (ByteArray) -> Unit) {
        val id = device.address
        if (id !in admitted) return
        if (!ingress.acquire(id)) return
        val copy = value.copyOf()
        post {
            try {
                block(copy)
            } finally {
                copy.fill(0)
                ingress.release(id)
            }
        }
    }

    private fun remove(id: String) {
        admitted.remove(id)
        transport.disconnect(id)
        devices.remove(id)
        subscribed.remove(id)
        connectedAt.remove(id)
        ready.remove(id)
    }
    private fun disconnect(id: String) {
        val device = devices[id]
        remove(id)
        if (device != null) server?.cancelConnection(device)
    }
    fun close() {
        closed = true
        ingress.close()
        admitted.clear()
        handler.removeCallbacksAndMessages(null)
        try {
            advertiser?.stopAdvertising(advertising)
        } catch (_: SecurityException) { /* Permission revoked. */ }
        devices.keys.toList().forEach { id ->
            try {
                disconnect(id)
            } catch (_: SecurityException) { /* Local state already erased. */ }
        }
        transport.clear()
        try {
            server?.clearServices()
        } catch (_: SecurityException) { /* Permission revoked. */ }
        try {
            server?.close()
        } catch (_: SecurityException) { /* Permission revoked. */ }
        server = null
    }

    companion object {
        val SERVICE_UUID: UUID = UUID.fromString("8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51")
        val WRITE_UUID: UUID = UUID.fromString("8e400001-8f3c-4d1a-b64a-8e3c9b0d2a51")
        val NOTIFY_UUID: UUID = UUID.fromString("8e400002-8f3c-4d1a-b64a-8e3c9b0d2a51")
        val CCCD_UUID: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")
    }
}
