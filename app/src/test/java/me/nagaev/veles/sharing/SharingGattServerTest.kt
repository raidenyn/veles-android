package me.nagaev.veles.sharing

import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothGattServer
import android.bluetooth.BluetoothGattServerCallback
import android.bluetooth.BluetoothGattService
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.le.BluetoothLeAdvertiser
import android.content.Context
import android.os.Looper
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.time.Duration

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class SharingGattServerTest {
    private val context = mockk<Context>()
    private val manager = mockk<BluetoothManager>()
    private val adapter = mockk<BluetoothAdapter>()
    private val advertiser = mockk<BluetoothLeAdvertiser>(relaxed = true)
    private val platform = mockk<BluetoothGattServer>(relaxed = true)
    private val callback = slot<BluetoothGattServerCallback>()
    private val service = slot<BluetoothGattService>()
    private val states = mutableListOf<SharingAvailability>()
    private var records = 0
    private lateinit var subject: SharingGattServer

    @Before fun setup() {
        every { context.getSystemService(BluetoothManager::class.java) } returns manager
        every { manager.adapter } returns adapter
        every { adapter.bluetoothLeAdvertiser } returns advertiser
        every { manager.openGattServer(context, capture(callback)) } returns platform
        every { platform.addService(capture(service)) } returns true
        subject = SharingGattServer(context, states::add) { _, _ ->
            records++
            byteArrayOf(7)
        }
        subject.start()
    }

    private fun device(id: String): BluetoothDevice = mockk<BluetoothDevice>().also { every { it.address } returns id }
    private fun flush() = shadowOf(Looper.getMainLooper()).idle()

    private fun subscribe(device: BluetoothDevice) {
        callback.captured.onConnectionStateChange(device, 0, BluetoothProfile.STATE_CONNECTED)
        callback.captured.onMtuChanged(device, 185)
        flush()
        val descriptor = service.captured.getCharacteristic(SharingGattServer.NOTIFY_UUID)
            .getDescriptor(SharingGattServer.CCCD_UUID)
        callback.captured.onDescriptorWriteRequest(
            device,
            9,
            descriptor,
            false,
            true,
            0,
            BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE,
        )
        flush()
    }

    @Test fun refusedAddressesCannotStarveSubscribedClient() {
        val a = device("a")
        subscribe(a)
        val write = service.captured.getCharacteristic(SharingGattServer.WRITE_UUID)
        repeat(8) {
            callback.captured.onCharacteristicWriteRequest(
                device("refused-$it"),
                0,
                write,
                false,
                false,
                0,
                byteArrayOf(0x80.toByte(), 1, 0, 1, 5),
            )
        }
        callback.captured.onCharacteristicWriteRequest(
            a,
            0,
            write,
            false,
            false,
            0,
            byteArrayOf(0x80.toByte(), 1, 0, 1, 5),
        )
        flush()
        assertEquals(1, records)
        subject.close()
    }

    @Test fun notificationFailureDisconnectsOnlyItsClient() {
        val a = device("a")
        val b = device("b")
        subscribe(a)
        subscribe(b)
        callback.captured.onNotificationSent(a, BluetoothGatt.GATT_FAILURE)
        flush()
        val write = service.captured.getCharacteristic(SharingGattServer.WRITE_UUID)
        callback.captured.onCharacteristicWriteRequest(
            b,
            0,
            write,
            false,
            false,
            0,
            byteArrayOf(0x80.toByte(), 1, 0, 1, 5),
        )
        flush()
        assertEquals(1, records)
        verify { platform.cancelConnection(a) }
        verify(exactly = 0) { platform.cancelConnection(b) }
        subject.close()
    }

    @Test fun closeIgnoresLateServiceAndConnectionCallbacks() {
        subject.close()
        callback.captured.onServiceAdded(BluetoothGatt.GATT_SUCCESS, service.captured)
        callback.captured.onConnectionStateChange(device("late"), 0, BluetoothProfile.STATE_CONNECTED)
        flush()
        verify(exactly = 0) { advertiser.startAdvertising(any(), any(), any()) }
        verify { platform.close() }
        assertEquals(emptyList<SharingAvailability>(), states)
    }

    @Test fun ninthClientRejectedAndMtuOnlyClientTimesOut() {
        val devices = (0..8).map { device("$it") }
        devices.forEach { callback.captured.onConnectionStateChange(it, 0, BluetoothProfile.STATE_CONNECTED) }
        flush()
        verify { platform.cancelConnection(devices[8]) }
        callback.captured.onMtuChanged(devices[0], 185)
        flush()
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(6))
        verify { platform.cancelConnection(devices[0]) }
        subject.close()
    }

    @Test fun invalidOperationsNeverReachRecordConsumer() {
        val device = device("a")
        callback.captured.onConnectionStateChange(device, 0, BluetoothProfile.STATE_CONNECTED)
        callback.captured.onMtuChanged(device, 185)
        flush()
        val write = service.captured.getCharacteristic(SharingGattServer.WRITE_UUID)
        callback.captured.onCharacteristicReadRequest(device, 1, 0, write)
        callback.captured.onExecuteWrite(device, 2, true)
        callback.captured.onCharacteristicWriteRequest(device, 3, write, true, true, 0, byteArrayOf(0x80.toByte(), 1, 0, 1, 5))
        flush()
        assertEquals(0, records)
        verify { platform.sendResponse(device, 1, BluetoothGatt.GATT_READ_NOT_PERMITTED, 0, null) }
        verify { platform.sendResponse(device, 2, BluetoothGatt.GATT_REQUEST_NOT_SUPPORTED, 0, null) }
        verify { platform.sendResponse(device, 3, BluetoothGatt.GATT_REQUEST_NOT_SUPPORTED, 0, null) }
        subject.close()
    }
}
