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
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
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
    private var response = byteArrayOf(7)
    private lateinit var subject: SharingGattServer

    @Before fun setup() {
        every { context.getSystemService(BluetoothManager::class.java) } returns manager
        every { manager.adapter } returns adapter
        every { adapter.bluetoothLeAdvertiser } returns advertiser
        every { manager.openGattServer(context, capture(callback)) } returns platform
        every { platform.addService(capture(service)) } returns true
        subject = SharingGattServer(context, states::add) { _, _ ->
            records++
            response.copyOf()
        }
        subject.start()
    }

    private fun device(id: String): BluetoothDevice = mockk<BluetoothDevice>().also { every { it.address } returns id }
    private fun flush() = shadowOf(Looper.getMainLooper()).idle()

    private fun subscribe(device: BluetoothDevice, mtu: Int = 185) {
        callback.captured.onConnectionStateChange(device, 0, BluetoothProfile.STATE_CONNECTED)
        callback.captured.onMtuChanged(device, mtu)
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

    @Test fun duplicateConnectedCallbackKeepsAHealthyLink() {
        val a = device("a")
        subscribe(a)
        callback.captured.onConnectionStateChange(a, 0, BluetoothProfile.STATE_CONNECTED)
        callback.captured.onConnectionStateChange(a, 0, BluetoothProfile.STATE_CONNECTED)
        flush()
        verify(exactly = 0) { platform.cancelConnection(a) }
        val write = service.captured.getCharacteristic(SharingGattServer.WRITE_UUID)
        callback.captured.onCharacteristicWriteRequest(a, 0, write, false, false, 0, byteArrayOf(0x80.toByte(), 1, 0, 1, 5))
        flush()
        assertEquals("the duplicate must not reset MTU/subscription state", 1, records)
        subject.close()
    }

    @Test fun capacityRefusalStillCancelsOnlyTheRefusedClient() {
        val devices = (0..8).map { device("$it") }
        devices.forEach { callback.captured.onConnectionStateChange(it, 0, BluetoothProfile.STATE_CONNECTED) }
        devices.take(8).forEach { callback.captured.onConnectionStateChange(it, 0, BluetoothProfile.STATE_CONNECTED) }
        flush()
        verify(exactly = 1) { platform.cancelConnection(devices[8]) }
        devices.take(8).forEach { verify(exactly = 0) { platform.cancelConnection(it) } }
        subject.close()
    }

    @Test fun subscriptionBeforeMtuIsRejectedAndRecordIoStaysGatedUntilMtuThenCccd() {
        val a = device("a")
        callback.captured.onConnectionStateChange(a, 0, BluetoothProfile.STATE_CONNECTED)
        flush()
        val descriptor = service.captured.getCharacteristic(SharingGattServer.NOTIFY_UUID)
            .getDescriptor(SharingGattServer.CCCD_UUID)
        val write = service.captured.getCharacteristic(SharingGattServer.WRITE_UUID)
        callback.captured.onDescriptorWriteRequest(a, 4, descriptor, false, true, 0, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE)
        flush()
        verify { platform.sendResponse(a, 4, BluetoothGatt.GATT_REQUEST_NOT_SUPPORTED, 0, null) }
        callback.captured.onCharacteristicWriteRequest(a, 0, write, false, false, 0, byteArrayOf(0x80.toByte(), 1, 0, 1, 5))
        flush()
        assertEquals("no record I/O before MTU and CCCD", 0, records)
        callback.captured.onMtuChanged(a, 517)
        callback.captured.onDescriptorWriteRequest(a, 5, descriptor, false, true, 0, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE)
        callback.captured.onCharacteristicWriteRequest(a, 0, write, false, false, 0, byteArrayOf(0x80.toByte(), 1, 0, 1, 5))
        flush()
        verify { platform.sendResponse(a, 5, BluetoothGatt.GATT_SUCCESS, 0, null) }
        assertEquals(1, records)
        subject.close()
    }

    @Test fun maximumRecordNotificationsNeverExceed512BytesAtMtu517() {
        response = ByteArray(4096) { (it * 7 + 1).toByte() }
        val sent = mutableListOf<ByteArray>()
        every { platform.notifyCharacteristicChanged(any(), any(), any(), capture(sent)) } returns 0
        val a = device("a")
        subscribe(a, 517)
        val write = service.captured.getCharacteristic(SharingGattServer.WRITE_UUID)
        callback.captured.onCharacteristicWriteRequest(a, 0, write, false, false, 0, byteArrayOf(0x80.toByte(), 1, 0, 1, 5))
        flush()
        repeat(9) {
            callback.captured.onNotificationSent(a, BluetoothGatt.GATT_SUCCESS)
            flush()
        }
        assertEquals(List(8) { 512 } + 36, sent.map { it.size })
        assertArrayEquals(response, sent.flatMap { it.drop(4) }.toByteArray())
        assertTrue(sent.all { it.size <= 512 })
        verify(exactly = 0) { platform.cancelConnection(a) }
        subject.close()
    }

    @Test fun inboundWritesAbove512BytesAreDroppedBeforeReachingTheTransport() {
        val a = device("a")
        subscribe(a, 517)
        val write = service.captured.getCharacteristic(SharingGattServer.WRITE_UUID)
        callback.captured.onCharacteristicWriteRequest(
            a,
            0,
            write,
            false,
            false,
            0,
            byteArrayOf(0x80.toByte(), 1, 0xff.toByte(), 0xff.toByte()) + ByteArray(509),
        )
        flush()
        assertEquals(0, records)
        // the exact-budget frame (512 bytes: 4 + 508) is accepted when it completes a record
        callback.captured.onCharacteristicWriteRequest(
            a,
            0,
            write,
            false,
            false,
            0,
            byteArrayOf(0x80.toByte(), 2, 0x01, 0xfc.toByte()) + ByteArray(508),
        )
        flush()
        assertEquals(1, records)
        subject.close()
    }

    @Test fun optInSyntheticPushFiresOncePerSubscriptionAndIsOffByDefault() {
        val sent = mutableListOf<ByteArray>()
        every { platform.notifyCharacteristicChanged(any(), any(), any(), capture(sent)) } returns 0
        val quiet = device("quiet")
        subscribe(quiet, 517)
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(3))
        assertEquals("off by default: nothing is pushed", 0, sent.size)
        SyntheticTransportFixture.unsolicitedAfterMillis = 2000
        SyntheticTransportFixture.unsolicitedEvidence.clear()
        try {
            val a = device("a")
            subscribe(a, 517)
            shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(1900))
            assertEquals("not before the configured delay", 0, sent.size)
            shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(200))
            repeat(9) {
                callback.captured.onNotificationSent(a, BluetoothGatt.GATT_SUCCESS)
                flush()
            }
            assertEquals(List(8) { 512 } + 36, sent.map { it.size })
            assertArrayEquals(SyntheticTransportFixture.unsolicited(), sent.flatMap { it.drop(4) }.toByteArray())
            val evidence = SyntheticTransportFixture.unsolicitedEvidence.single()
            assertEquals(true, evidence.queued)
            assertEquals(SyntheticTransportFixture.sha256Hex(SyntheticTransportFixture.unsolicited()), evidence.sha256)
            assertTrue("peers are hashed, not raw addresses", evidence.peer.length == 8 && evidence.peer != "a")
        } finally {
            SyntheticTransportFixture.unsolicitedAfterMillis = null
            SyntheticTransportFixture.unsolicitedEvidence.clear()
            subject.close()
        }
    }

    private fun frame(index: Int, final: Boolean, total: Int, payload: Int) = byteArrayOf(
        (index or if (final) 0x80 else 0).toByte(),
        1,
        (total shr 8).toByte(),
        total.toByte(),
    ) + ByteArray(payload) { 9 }

    private fun write(device: BluetoothDevice, value: ByteArray) {
        val characteristic = service.captured.getCharacteristic(SharingGattServer.WRITE_UUID)
        callback.captured.onCharacteristicWriteRequest(device, 0, characteristic, false, false, 0, value)
    }

    @Test fun controlPartialRecordCompletesWithoutAMalformedFrame() {
        val a = device("a")
        subscribe(a, 517)
        write(a, frame(0, false, 509, 508))
        write(a, frame(1, true, 0, 1))
        flush()
        assertEquals("the control sequence must deliver, or the next test proves nothing", 1, records)
        subject.close()
    }

    @Test fun overBudgetFrameDiscardsTheEarlierPartialSoItCanNeverCompleteAcrossIt() {
        val a = device("a")
        subscribe(a, 517)
        write(a, frame(0, false, 509, 508))
        write(a, ByteArray(513) { 7 }) // 513 > 512: dropped before copying, but still malformed
        write(a, frame(1, true, 0, 1))
        flush()
        assertEquals("old partial must not be delivered", 0, records)
        // the peer is not punished beyond the discard: a fresh transaction completes normally
        write(a, byteArrayOf(0x80.toByte(), 2, 0, 1, 5))
        flush()
        assertEquals(1, records)
        subject.close()
    }

    @Test fun malformedFramesAreOrderedAndBoundedPerPeer() {
        val a = device("a")
        val b = device("b")
        subscribe(a, 517)
        subscribe(b, 517)
        write(b, frame(0, false, 509, 508))
        // a flood of oversize values from `a` is bounded by its ingress budget (and fails `a` closed); it never touches `b`
        repeat(500) { write(a, ByteArray(600)) }
        write(b, frame(1, true, 0, 1))
        flush()
        assertEquals("another peer's partial is unaffected", 1, records)
        // the flood saturated a's ingress, so a was failed closed (bounded, not queued forever); it reconnects
        verify(exactly = 1) { platform.cancelConnection(a) }
        subscribe(a, 517)
        // valid frames queued BEFORE the oversize one still count; only later fragments are cut off
        write(a, frame(0, false, 509, 508))
        write(a, frame(1, true, 0, 1))
        write(a, ByteArray(513))
        flush()
        assertEquals(2, records)
        subject.close()
    }

    @Test fun saturatedIngressFailsTheMalformedPeerClosedInsteadOfDroppingTheDiscard() {
        val a = device("a")
        val b = device("b")
        subscribe(a, 517)
        subscribe(b, 517)
        // No flush between writes: all of a's callbacks stay pending, so its 16-slot ingress budget fills.
        write(a, frame(0, false, 509, 508)) // 1: the partial that must never complete
        repeat(15) { write(a, frame(1, true, 0, 0).also { it[1] = 9 }) } // 15 valid stray frames, ignored
        write(a, ByteArray(513)) // cannot acquire a slot: the discard cannot be queued
        flush() // drain everything
        write(a, frame(1, true, 0, 1)) // otherwise-valid final for the old partial
        flush()
        assertEquals("the old partial must never be delivered", 0, records)
        verify { platform.cancelConnection(a) }
        verify(exactly = 0) { platform.cancelConnection(b) }
        write(b, byteArrayOf(0x80.toByte(), 3, 0, 1, 5))
        flush()
        assertEquals("the unaffected peer still works", 1, records)
        subject.close()
    }

    @Test fun overflowDisconnectIsQueuedAtMostOncePerPeerAndClearedOnReconnect() {
        val a = device("a")
        subscribe(a, 517)
        repeat(16) { write(a, frame(1, true, 0, 0).also { it[1] = 9 }) }
        repeat(50) { write(a, ByteArray(600)) }
        flush()
        verify(exactly = 1) { platform.cancelConnection(a) }
        // a reconnect starts clean: nothing stale from the overflow remains
        subscribe(a, 517)
        write(a, byteArrayOf(0x80.toByte(), 4, 0, 1, 5))
        flush()
        assertEquals(1, records)
        subject.close()
    }

    @Test fun oversizedValueOnAnInvalidOperationIsRejectedWithoutTouchingThePartial() {
        val a = device("a")
        subscribe(a, 517)
        val write = service.captured.getCharacteristic(SharingGattServer.WRITE_UUID)
        write(a, frame(0, false, 509, 508))
        callback.captured.onCharacteristicWriteRequest(a, 77, write, false, true, 0, ByteArray(513))
        write(a, frame(1, true, 0, 1))
        flush()
        verify { platform.sendResponse(a, 77, BluetoothGatt.GATT_REQUEST_NOT_SUPPORTED, 0, null) }
        assertEquals("an invalid operation is not a malformed frame: the partial is untouched", 1, records)
        subject.close()
    }
}
