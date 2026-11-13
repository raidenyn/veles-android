package me.nagaev.veles.sharing

import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
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
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class SharingGattServerTelemetryOffTest {
    private val context = mockk<Context>()
    private val manager = mockk<BluetoothManager>()
    private val adapter = mockk<BluetoothAdapter>()
    private val advertiser = mockk<BluetoothLeAdvertiser>(relaxed = true)
    private val platform = mockk<BluetoothGattServer>(relaxed = true)
    private val callback = slot<BluetoothGattServerCallback>()
    private val service = slot<BluetoothGattService>()
    private var records = 0
    private lateinit var subject: SharingGattServer

    @Before fun setup() {
        every { context.getSystemService(BluetoothManager::class.java) } returns manager
        every { manager.adapter } returns adapter
        every { adapter.bluetoothLeAdvertiser } returns advertiser
        every { manager.openGattServer(context, capture(callback)) } returns platform
        every { platform.addService(capture(service)) } returns true
        SyntheticTransportFixture.telemetry = null
        subject = SharingGattServer(
            context,
            {},
            object : ConnectionListener {
                override fun onConnected(gen: ConnectionGeneration) = Unit
                override fun onRecord(gen: ConnectionGeneration, bytes: ByteArray, admission: RecordAdmission) {
                    records++
                    try {
                        subject.send(gen, byteArrayOf(7), null)
                    } finally {
                        bytes.fill(0)
                        admission.release()
                    }
                }
                override fun onDisconnected(gen: ConnectionGeneration) = Unit
                override fun onSendComplete(gen: ConnectionGeneration, token: Long, ok: Boolean) = Unit
            },
        )
        subject.start()
    }

    private fun device(id: String): BluetoothDevice = mockk<BluetoothDevice>().also { every { it.address } returns id }
    private fun flush() = shadowOf(Looper.getMainLooper()).idle()

    private fun queuedInboundSize(): Int {
        val field = subject.javaClass.getDeclaredField("queuedInbound").apply { isAccessible = true }
        return (field.get(subject) as Map<*, *>).size
    }

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

    @Test fun disabledTelemetryDoesNoQueuedInboundBookkeeping() {
        try {
            val a = device("a")
            subscribe(a)
            val write = service.captured.getCharacteristic(SharingGattServer.WRITE_UUID)
            callback.captured.onCharacteristicWriteRequest(a, 0, write, false, false, 0, byteArrayOf(0x80.toByte(), 1, 0, 1, 5))
            assertEquals("frame stays queued until the looper runs", 0, records)
            assertEquals("diagnostics are inactive unless opted in: no inbound bookkeeping", 0, queuedInboundSize())
            flush()
            assertEquals("transport behavior is preserved", 1, records)
        } finally {
            subject.close()
            SyntheticTransportFixture.telemetry = null
        }
    }

    @Test fun disabledTelemetryCloseLeavesNoDroppedEvidenceBehind() {
        try {
            val a = device("a")
            subscribe(a)
            val write = service.captured.getCharacteristic(SharingGattServer.WRITE_UUID)
            callback.captured.onCharacteristicWriteRequest(a, 0, write, false, false, 0, byteArrayOf(0x80.toByte(), 1, 0, 1, 5))
            assertEquals(0, records)
            val telemetry = TransportTelemetry("test", { 1L }, { 2L })
            SyntheticTransportFixture.telemetry = telemetry
            subject.close()
            assertTrue(
                "a frame accepted while diagnostics were off must leave no queued evidence",
                telemetry.snapshot().peers.flatMap { it.events }.none { it.kind == "fragment_dropped" },
            )
            assertEquals(0, records)
        } finally {
            SyntheticTransportFixture.telemetry = null
        }
    }
}
