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
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.cancel
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import me.nagaev.veles.session.IdentitySigner
import me.nagaev.veles.session.PeerLookup
import me.nagaev.veles.session.PhoneSessionEngine
import me.nagaev.veles.trust.ActivationStep
import me.nagaev.veles.trust.InstallationIdentity
import me.nagaev.veles.trust.InstallationTrustRepository
import me.nagaev.veles.trust.PendingPeer
import me.nagaev.veles.trust.TrustedPeer
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.time.Duration

@Suppress("LargeClass") // One GATT server harness: transport, admission, fragmentation and session adapter.
@OptIn(ExperimentalCoroutinesApi::class)
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
    private var suppressReply = false
    private val connections = mutableListOf<ConnectionGeneration>()
    private val disconnections = mutableListOf<ConnectionGeneration>()
    private val completions = mutableListOf<Pair<Long, Boolean>>()
    private lateinit var subject: SharingGattServer

    /** Stand-in for the session host: consumes records and answers through the transport. */
    private val listener = object : ConnectionListener {
        override fun onConnected(gen: ConnectionGeneration) {
            connections += gen
        }

        override fun onRecord(gen: ConnectionGeneration, bytes: ByteArray, admission: RecordAdmission) {
            records++
            try {
                if (!suppressReply) subject.send(gen, response.copyOf(), null)
            } finally {
                bytes.fill(0)
                admission.release()
            }
        }

        override fun onDisconnected(gen: ConnectionGeneration) {
            disconnections += gen
        }

        override fun onSendComplete(gen: ConnectionGeneration, token: Long, ok: Boolean) {
            completions += token to ok
        }
    }

    @Before fun setup() {
        every { context.getSystemService(BluetoothManager::class.java) } returns manager
        every { manager.adapter } returns adapter
        every { adapter.bluetoothLeAdvertiser } returns advertiser
        every { manager.openGattServer(context, capture(callback)) } returns platform
        every { platform.addService(capture(service)) } returns true
        subject = start(listener)
    }

    private fun start(listener: ConnectionListener): SharingGattServer =
        SharingGattServer(context, states::add, listener).also { it.start() }

    private fun admission(): RecordAdmission = RecordAdmission { }

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

    @Test fun inboundEvidenceRecordsReceiptCompletionAndReplyQueue() {
        val telemetry = TransportTelemetry("test", { 1L }, { 2L })
        SyntheticTransportFixture.telemetry = telemetry
        try {
            val peer = device("a")
            subscribe(peer)
            val write = service.captured.getCharacteristic(SharingGattServer.WRITE_UUID)
            callback.captured.onCharacteristicWriteRequest(peer, 0, write, false, false, 0, byteArrayOf(0x80.toByte(), 1, 0, 1, 5))
            flush()
            val events = telemetry.snapshot().peers.single().events
            assertTrue(events.any { it.kind == "fragment_received" })
            assertTrue(events.any { it.kind == "fragment_processed" && it.label == "complete" })
            assertTrue(events.any { it.kind == "record_complete" && it.sha256 == SyntheticTransportFixture.sha256Hex(byteArrayOf(5)) })
            assertTrue(events.any { it.kind == "reply_queue" && it.label == "accepted" })
        } finally {
            subject.close()
            SyntheticTransportFixture.telemetry = null
        }
    }

    @Test fun replyQueueEvidenceNamesPeerByteLimit() {
        val telemetry = TransportTelemetry("test", { 1L }, { 2L })
        SyntheticTransportFixture.telemetry = telemetry
        try {
            response = ByteArray(4096)
            val peer = device("a")
            subscribe(peer)
            val write = service.captured.getCharacteristic(SharingGattServer.WRITE_UUID)
            repeat(5) {
                callback.captured.onCharacteristicWriteRequest(
                    peer,
                    0,
                    write,
                    false,
                    false,
                    0,
                    byteArrayOf(0x80.toByte(), (it + 1).toByte(), 0, 1, 5),
                )
                flush()
            }
            val queueEvents = telemetry.snapshot().peers.single().events.filter { it.kind == "reply_queue" }
            assertEquals(listOf("accepted", "accepted", "accepted", "accepted", "peer_byte_limit"), queueEvents.map { it.label })
        } finally {
            subject.close()
            SyntheticTransportFixture.telemetry = null
        }
    }

    @Test fun inboundEvidenceDistinguishesNullReplyAndInvalidOperation() {
        val telemetry = TransportTelemetry("test", { 1L }, { 2L })
        SyntheticTransportFixture.telemetry = telemetry
        try {
            suppressReply = true
            val peer = device("a")
            subscribe(peer)
            val write = service.captured.getCharacteristic(SharingGattServer.WRITE_UUID)
            val frame = byteArrayOf(0x80.toByte(), 1, 0, 1, 5)
            callback.captured.onCharacteristicWriteRequest(peer, 0, write, false, false, 0, frame)
            callback.captured.onCharacteristicWriteRequest(peer, 1, write, true, false, 0, frame)
            flush()
            val events = telemetry.snapshot().peers.single().events
            assertTrue("the record reaches the listener exactly once", events.count { it.kind == "record_delegated" } == 1)
            assertTrue(events.none { it.kind == "reply_queue" })
            assertTrue(events.any { it.kind == "fragment_rejected" && it.label == "invalid_operation_or_not_ready" })
        } finally {
            subject.close()
            SyntheticTransportFixture.telemetry = null
        }
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

    private fun stray() = frame(1, true, 0, 0).also { it[1] = 9 } // valid, ignored: no such transaction

    private fun saturatedMalformedScenario(mtu: Int, partialTotal: Int, partialPayload: Int, malformed: ByteArray?): Int {
        val a = device("a")
        val b = device("b")
        subscribe(a, mtu)
        subscribe(b, mtu)
        write(a, frame(0, false, partialTotal, partialPayload)) // 1 pending: the partial
        repeat(15) { write(a, stray()) } // 15 ignored stray continuations: 16 slots full
        if (malformed != null) write(a, malformed) // cannot acquire a slot, must not be silently lost
        flush() // drain
        write(a, frame(1, true, 0, partialTotal - partialPayload)) // otherwise-valid final
        flush()
        if (malformed != null) {
            verify(exactly = 1) { platform.cancelConnection(a) }
        } else {
            verify(exactly = 0) { platform.cancelConnection(a) }
        }
        verify(exactly = 0) { platform.cancelConnection(b) }
        write(b, byteArrayOf(0x80.toByte(), 3, 0, 1, 5))
        flush()
        subject.close()
        return records
    }

    @Test fun controlAtMtu185PartialCompletesWithoutTheMalformedFrame() {
        assertEquals(2, saturatedMalformedScenario(185, 179, 178, null)) // a's record + b's record
    }

    @Test fun oversizeForMtu185FrameOf183BytesUnderSaturationNeverLetsTheOldPartialComplete() {
        // 183 > attribute budget 182 at MTU 185 but < 512, so it takes the normal admission path
        assertEquals("only b's record", 1, saturatedMalformedScenario(185, 179, 178, ByteArray(183)))
    }

    @Test fun controlAtMtu517PartialCompletesWithoutTheMalformedFrame() {
        assertEquals(2, saturatedMalformedScenario(517, 509, 508, null))
    }

    @Test fun headerlessFrameUnderSaturationNeverLetsTheOldPartialCompleteAtMtu517() {
        assertEquals("only b's record", 1, saturatedMalformedScenario(517, 509, 508, ByteArray(3)))
    }

    @Test fun manyRefusedAdmissionsQueueOneDisconnectAndReconnectIsClean() {
        val a = device("a")
        subscribe(a, 185)
        repeat(16) { write(a, stray()) }
        repeat(50) { write(a, ByteArray(183)) } // all refused: still ONE disconnect
        flush()
        verify(exactly = 1) { platform.cancelConnection(a) }
        subscribe(a, 185)
        write(a, byteArrayOf(0x80.toByte(), 4, 0, 1, 5))
        flush()
        assertEquals(1, records)
        subject.close()
    }

    private fun TransportTelemetry.events() = snapshot().peers.flatMap { it.events }
    private fun TransportTelemetry.processedLabels() = events().filter { it.kind == "fragment_processed" }.map { it.label }
    private fun TransportTelemetry.eventLabels(kind: String) = events().filter { it.kind == kind }.map { it.label }

    private fun withTelemetry(block: (TransportTelemetry) -> Unit) {
        val telemetry = TransportTelemetry("test", { 1L }, { 2L })
        SyntheticTransportFixture.telemetry = telemetry
        try {
            block(telemetry)
        } finally {
            subject.close()
            SyntheticTransportFixture.telemetry = null
        }
    }

    private fun writeFrame(device: BluetoothDevice, frame: ByteArray) {
        write(device, frame)
        flush()
    }

    @Test fun inboundEvidenceDistinguishesEveryNegativeTransportOutcome() = withTelemetry { telemetry ->
        val a = device("a")
        subscribe(a, 185)
        writeFrame(a, ByteArray(183)) // above the MTU 185 attribute budget, below the 512 cap
        writeFrame(a, byteArrayOf(0x80.toByte(), 0, 0, 1, 5)) // transaction 0
        writeFrame(a, byteArrayOf(0, 2, 0, 0, 5)) // declared length 0
        writeFrame(a, byteArrayOf(0x81.toByte(), 3, 0, 0, 5)) // continuation without a partial
        writeFrame(a, byteArrayOf(0, 4, 0, 179.toByte()) + ByteArray(178)) // live partial
        writeFrame(a, byteArrayOf(0x82.toByte(), 4, 0, 0, 9)) // out of order
        writeFrame(a, byteArrayOf(0, 5, 0, 179.toByte()) + ByteArray(100)) // short non-final payload
        writeFrame(a, byteArrayOf(0, 6, 0, 179.toByte()) + ByteArray(178)) // live partial
        writeFrame(a, byteArrayOf(0, 6, 0, 179.toByte()) + ByteArray(178)) // duplicate start

        assertEquals(
            listOf(
                "invalid_frame_size",
                "invalid_transaction",
                "invalid_length",
                "missing_partial",
                "partial",
                "invalid_order",
                "invalid_payload_size",
                "partial",
                "conflict",
            ),
            telemetry.processedLabels(),
        )
        val events = telemetry.events()
        assertTrue("rejected frames never complete a record", events.none { it.kind == "record_complete" })
        assertTrue("rejected frames never generate a reply", events.none { it.kind == "reply_generated" })
        assertEquals(0, records)
        verify(exactly = 0) { platform.cancelConnection(a) }
    }

    @Test fun allocationRefusalIsReportedWhileLivePartialsStillComplete() = withTelemetry { telemetry ->
        val a = device("a")
        subscribe(a, 185)
        writeFrame(a, byteArrayOf(0, 1, 0, 200.toByte()) + ByteArray(178))
        writeFrame(a, byteArrayOf(0, 2, 0, 200.toByte()) + ByteArray(178))
        writeFrame(a, byteArrayOf(0, 3, 0, 200.toByte()) + ByteArray(178))
        assertEquals(listOf("partial", "partial", "allocation_limit"), telemetry.processedLabels())
        assertEquals("a refused start is not a delivery", 0, records)
        assertEquals(0, telemetry.eventLabels("record_complete").size)
        verify(exactly = 0) { platform.cancelConnection(a) }

        writeFrame(a, byteArrayOf(0x81.toByte(), 1, 0, 0) + ByteArray(22))
        writeFrame(a, byteArrayOf(0x81.toByte(), 2, 0, 0) + ByteArray(22))
        assertEquals("both live partials survived the refused third start", 2, records)
        assertEquals(listOf("accepted", "accepted"), telemetry.eventLabels("reply_queue"))
    }

    @Test fun inboundEvidenceSeparatesUnadmittedAndOversizedValues() = withTelemetry { telemetry ->
        val a = device("a")
        subscribe(a, 517)
        val write = service.captured.getCharacteristic(SharingGattServer.WRITE_UUID)
        callback.captured.onCharacteristicWriteRequest(
            device("stranger"),
            0,
            write,
            false,
            false,
            0,
            byteArrayOf(0x80.toByte(), 1, 0, 1, 5),
        )
        flush()
        callback.captured.onCharacteristicWriteRequest(a, 0, write, false, false, 0, ByteArray(513) { 7 })
        flush()

        assertEquals(listOf("not_admitted_or_overflowed"), telemetry.eventLabels("fragment_dropped"))
        assertEquals(listOf("oversized"), telemetry.eventLabels("fragment_rejected"))
        val oversized = telemetry.events().single { it.kind == "fragment_received" && it.bytes == 513 }
        assertNull("an oversized value is never hashed or retained", oversized.sha256)
        assertEquals(0, records)
    }

    @Test fun ingressSaturationIsReportedAtTheDroppedFrameBoundary() = withTelemetry { telemetry ->
        val a = device("a")
        val b = device("b")
        subscribe(a, 185)
        subscribe(b, 185)
        write(a, frame(0, false, 179, 178))
        repeat(15) { write(a, stray()) }
        write(a, ByteArray(183)) // no ingress slot left: the frame cannot be copied or processed
        flush()
        assertEquals(listOf("ingress_limit"), telemetry.eventLabels("fragment_dropped"))
        verify(exactly = 1) { platform.cancelConnection(a) }
        verify(exactly = 0) { platform.cancelConnection(b) }
    }

    @Test fun closeReportsQueuedInboundWorkAsDroppedInsteadOfSilentlyRemovingIt() = withTelemetry { telemetry ->
        val a = device("a")
        subscribe(a)
        write(a, byteArrayOf(0x80.toByte(), 1, 0, 1, 5))
        assertEquals("frame stays queued until the looper runs", 0, records)

        subject.close()

        val events = telemetry.events()
        assertTrue(events.any { it.kind == "fragment_received" })
        assertTrue("queued work must not be reported as processed", events.none { it.kind == "fragment_processed" })
        val dropped = events.single { it.kind == "fragment_dropped" }
        assertEquals("server_closed", dropped.label)
        assertEquals(5, dropped.bytes)
        assertEquals(0, records)
    }

    @Test fun finalSnapshotAfterCloseKeepsIncompleteAndLateCallbackEvidence() = withTelemetry { telemetry ->
        val a = device("a")
        subscribe(a)
        writeFrame(a, byteArrayOf(0x80.toByte(), 1, 0, 1, 5))
        assertEquals(1, telemetry.snapshot().peers.single().pendingUnresolved)
        assertEquals(1, records)

        subject.close()
        callback.captured.onNotificationSent(a, BluetoothGatt.GATT_SUCCESS)
        flush()

        val snapshot = telemetry.snapshot()
        val peer = snapshot.peers.single()
        assertEquals("the pending callback is incomplete evidence, not a loss", 1, peer.pendingUnresolved)
        assertEquals(1, peer.callbacksAfterClose)
        assertEquals(0, peer.callbacks)
        assertFalse(snapshot.clean)
        assertTrue(telemetry.events().any { it.kind == "callback_after_close" })
        assertTrue(
            "the main-looper hop was closed, so it must not be reported as processed",
            telemetry.events().none { it.kind == "notify_callback" },
        )
    }

    @Test fun echoBytesOnNormalPathGetNoPlaintextReply() {
        TransportMode.instrumentationEcho = false
        val scope = CoroutineScope(UnconfinedTestDispatcher())
        try {
            val vel = byteArrayOf(0x56, 0x45, 0x4c, 1)

            // The instrumentation echo is inert unless its mode is explicitly enabled.
            val echoed = mutableListOf<ByteArray>()
            val echo = InstrumentationEchoListener(
                object : SessionTransport {
                    override fun send(gen: ConnectionGeneration, bytes: ByteArray, token: Long?): Boolean {
                        echoed += bytes.copyOf()
                        return true
                    }

                    override fun close(gen: ConnectionGeneration) = Unit
                },
            )
            echo.onRecord(ConnectionGeneration("a", 9), vel.copyOf(), admission())
            assertTrue("the echo listener must never answer beside the session engine", echoed.isEmpty())

            subject.close()
            val sent = mutableListOf<ByteArray>()
            every { platform.notifyCharacteristicChanged(any(), any(), any(), capture(sent)) } returns 0
            lateinit var server: SharingGattServer
            val transport = object : SessionTransport {
                override fun send(gen: ConnectionGeneration, bytes: ByteArray, token: Long?): Boolean =
                    server.send(gen, bytes, token)

                override fun close(gen: ConnectionGeneration) = server.close(gen)
            }
            val host = SessionHost(
                scope,
                transport,
                {
                    PhoneSessionEngine(
                        localInstallationId = ByteArray(16),
                        signer = IdentitySigner { error("an unknown peer is never signed for") },
                        lookup = PeerLookup { null },
                        clock = { 0L },
                        nonce = { ByteArray(16) },
                    )
                },
                NoTrust,
                { 0L },
            )
            server = start(host)
            subject = server
            val peer = device("a")
            subscribe(peer)
            val write = service.captured.getCharacteristic(SharingGattServer.WRITE_UUID)
            callback.captured.onCharacteristicWriteRequest(peer, 0, write, false, false, 0, byteArrayOf(0x80.toByte(), 1, 0, 4) + vel)
            flush()

            val reply = sent.single().copyOfRange(4, sent.single().size)
            assertTrue("no plaintext echo is ever queued", reply.contentEquals(byteArrayOf(0x0F, 0, 0)))
            assertFalse("the plaintext VEL bytes never reach the wire", reply.contentEquals(vel))
            callback.captured.onNotificationSent(peer, BluetoothGatt.GATT_SUCCESS)
            flush()
            verify { platform.cancelConnection(peer) }
        } finally {
            TransportMode.instrumentationEcho = false
            scope.cancel()
            subject.close()
        }
    }

    @Test fun recordAdmissionsBoundQueuedInboundWorkUntilReleased() {
        subject.close()
        val held = mutableListOf<RecordAdmission>()
        val delivered = mutableListOf<Int>()
        subject = start(
            object : ConnectionListener {
                override fun onConnected(gen: ConnectionGeneration) = Unit

                override fun onRecord(gen: ConnectionGeneration, bytes: ByteArray, admission: RecordAdmission) {
                    delivered += bytes.size
                    bytes.fill(0)
                    held += admission // the engine is still working: the ingress slot stays taken
                }

                override fun onDisconnected(gen: ConnectionGeneration) = Unit
                override fun onSendComplete(gen: ConnectionGeneration, token: Long, ok: Boolean) = Unit
            },
        )
        val a = device("a")
        subscribe(a, 517)
        val write = service.captured.getCharacteristic(SharingGattServer.WRITE_UUID)
        repeat(16) { index ->
            callback.captured.onCharacteristicWriteRequest(
                a,
                index,
                write,
                false,
                false,
                0,
                byteArrayOf(0x80.toByte(), (index + 1).toByte(), 0, 1, 5),
            )
        }
        callback.captured.onCharacteristicWriteRequest(a, 99, write, false, false, 0, byteArrayOf(0x80.toByte(), 17, 0, 1, 5))
        flush()

        assertEquals("held admissions bound the queued records", 16, delivered.size)
        verify { platform.cancelConnection(a) }
        held.forEach { it.release() }
        assertEquals(16, held.size)
        subject.close()
    }

    @Test fun oldGenerationSendIsRefusedAfterReconnect() {
        val a = device("a")
        subscribe(a, 517)
        val old = connections.last()
        callback.captured.onConnectionStateChange(a, 0, BluetoothProfile.STATE_DISCONNECTED)
        flush()
        subscribe(a, 517)
        val fresh = connections.last()
        assertNotEquals("reconnect always starts a new generation", old.serial, fresh.serial)

        val sent = mutableListOf<ByteArray>()
        every { platform.notifyCharacteristicChanged(any(), any(), any(), capture(sent)) } returns 0
        assertFalse("an ended generation can never enqueue", subject.send(old, byteArrayOf(1, 2, 3, 4), 1L))
        flush()
        assertTrue(sent.isEmpty())

        assertTrue(subject.send(fresh, byteArrayOf(5, 6, 7, 8), 2L))
        flush()
        assertEquals(1, sent.size)
        callback.captured.onNotificationSent(a, BluetoothGatt.GATT_SUCCESS)
        flush()
        assertEquals(listOf(2L to true), completions)
    }

    @Test fun staleNotificationCallbackNeverAcknowledgesTheNewGeneration() {
        val sent = mutableListOf<ByteArray>()
        every { platform.notifyCharacteristicChanged(any(), any(), any(), capture(sent)) } returns 0
        val a = device("a")
        subscribe(a, 517)
        val old = connections.last()
        assertTrue(subject.send(old, ByteArray(1016) { 3 }, 77L))
        flush()
        assertEquals("the first fragment is in flight for the old generation", 1, sent.size)

        // Link loss before the callback, then a new generation takes the same address.
        callback.captured.onConnectionStateChange(a, 0, BluetoothProfile.STATE_DISCONNECTED)
        flush()
        subscribe(a, 517)
        val fresh = connections.last()
        assertEquals("the dropped tokened record is reported failed", listOf(77L to false), completions)

        assertTrue(subject.send(fresh, ByteArray(1016) { 9 }, 88L))
        flush()
        assertEquals("the replacement waits for the old fragment's callback", 1, sent.size)

        callback.captured.onNotificationSent(a, BluetoothGatt.GATT_SUCCESS)
        flush()
        assertEquals("the stale ack is consumed and releases the replacement", 2, sent.size)
        assertEquals("a delayed ack cannot complete anything", listOf(77L to false), completions)

        callback.captured.onNotificationSent(a, BluetoothGatt.GATT_SUCCESS)
        flush()
        callback.captured.onNotificationSent(a, BluetoothGatt.GATT_SUCCESS)
        flush()
        assertEquals("the new generation completes its own record only", listOf(77L to false, 88L to true), completions)
    }

    @Test fun staleCallbackAfterQuarantineTimeoutFailsTheNewLinkClosed() {
        val sent = mutableListOf<ByteArray>()
        every { platform.notifyCharacteristicChanged(any(), any(), any(), capture(sent)) } returns 0
        val a = device("a")
        subscribe(a, 517)
        val old = connections.last()
        assertTrue(subject.send(old, ByteArray(1016) { 3 }, 77L))
        flush()
        assertEquals(1, sent.size)

        callback.captured.onConnectionStateChange(a, 0, BluetoothProfile.STATE_DISCONNECTED)
        flush()
        subscribe(a, 517)
        val fresh = connections.last()
        assertEquals(listOf(77L to false), completions)
        assertTrue(subject.send(fresh, ByteArray(20) { 4 }, 88L))
        flush()
        assertEquals("the quarantine holds the replacement", 1, sent.size)

        // No stale ack arrived within the quarantine: the sweep releases the pump but must
        // leave the address ambiguous for the next callback.
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(2))
        assertEquals("the replacement may send again", 2, sent.size)

        callback.captured.onNotificationSent(a, BluetoothGatt.GATT_SUCCESS)
        flush()
        assertEquals(
            "an ambiguous callback never completes a record",
            listOf(77L to false, 88L to false),
            completions,
        )
        verify { platform.cancelConnection(a) }
    }

    @Test fun secondReconnectKeepsFencingForTheDisconnectedReplacement() {
        val sent = mutableListOf<ByteArray>()
        every { platform.notifyCharacteristicChanged(any(), any(), any(), capture(sent)) } returns 0
        val a = device("a")
        subscribe(a, 517)
        val first = connections.last()
        assertTrue(subject.send(first, ByteArray(1016) { 3 }, 77L))
        flush()
        assertEquals(1, sent.size)
        callback.captured.onConnectionStateChange(a, 0, BluetoothProfile.STATE_DISCONNECTED)
        flush()

        // Second generation: blocked by the first generation's quarantine.
        subscribe(a, 517)
        val second = connections.last()
        assertTrue(subject.send(second, ByteArray(20) { 4 }, 88L))
        flush()
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(2))
        assertEquals("the expired quarantine releases the second generation once", 2, sent.size)

        // The delayed first-generation ack is ambiguous: it closes the second generation without
        // completing anything, but the second generation's own flight must stay fenced.
        callback.captured.onNotificationSent(a, BluetoothGatt.GATT_SUCCESS)
        flush()
        assertEquals(listOf(77L to false, 88L to false), completions)

        // Third generation: it may not send until the second generation's ack is consumed, and
        // that ack must never complete the third generation's record.
        subscribe(a, 517)
        val third = connections.last()
        assertTrue(subject.send(third, ByteArray(20) { 6 }, 99L))
        flush()
        assertEquals("the second generation's outstanding ack fences the third", 2, sent.size)

        callback.captured.onNotificationSent(a, BluetoothGatt.GATT_SUCCESS)
        flush()
        assertEquals("a delayed replacement ack completes nothing", listOf(77L to false, 88L to false), completions)
        assertEquals("consuming it releases the third generation", 3, sent.size)

        callback.captured.onNotificationSent(a, BluetoothGatt.GATT_SUCCESS)
        flush()
        assertEquals(listOf(77L to false, 88L to false, 99L to true), completions)
    }

    @Test fun duplicateNotificationCallbackNeverAcknowledgesASecondRecord() {
        val sent = mutableListOf<ByteArray>()
        every { platform.notifyCharacteristicChanged(any(), any(), any(), capture(sent)) } returns 0
        val a = device("a")
        subscribe(a, 517)
        val gen = connections.last()
        assertTrue(subject.send(gen, ByteArray(20) { 1 }, 5L))
        flush()
        callback.captured.onNotificationSent(a, BluetoothGatt.GATT_SUCCESS)
        flush()
        assertEquals(listOf(5L to true), completions)

        // Nothing else is queued: a repeated success cannot fabricate a completion.
        callback.captured.onNotificationSent(a, BluetoothGatt.GATT_SUCCESS)
        flush()
        assertEquals(listOf(5L to true), completions)

        assertTrue(subject.send(gen, ByteArray(20) { 2 }, 6L))
        flush()
        callback.captured.onNotificationSent(a, BluetoothGatt.GATT_SUCCESS)
        flush()
        assertEquals(listOf(5L to true, 6L to true), completions)
    }
}

private object NoTrust : InstallationTrustRepository {
    override suspend fun identity(): InstallationIdentity = error("no trust in this fixture")
    override suspend fun peers(): List<TrustedPeer> = emptyList()
    override suspend fun activePeers(): List<TrustedPeer> = emptyList()
    override suspend fun addPending(peer: PendingPeer) = error("no trust in this fixture")
    override suspend fun confirm(peerId: String, step: ActivationStep, proof: ByteArray) = error("no trust in this fixture")
    override suspend fun revoke(peerId: String) = error("no trust in this fixture")
    override suspend fun markAuthenticated(peerId: String) = Unit
}
