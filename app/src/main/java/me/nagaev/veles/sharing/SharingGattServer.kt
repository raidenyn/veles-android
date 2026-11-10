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

/**
 * All platform operations are confined to the main looper. Callbacks are generation-local: every
 * admitted link gets a [ConnectionGeneration] and the listener receives one ordered effect
 * stream for it. [send]/[close] may be called from the session host's threads; admission and the
 * platform work they trigger stay safe (the byte transport is synchronized, the pump is posted).
 */
@Suppress("TooManyFunctions") // GATT lifecycle, admission, fragmentation plumbing and session transport.
internal class SharingGattServer(
    private val context: Context,
    private val onState: (SharingAvailability) -> Unit,
    private val listener: ConnectionListener,
) : SessionTransport {
    private val handler = Handler(Looper.getMainLooper())
    private val transport = GattTransport(SystemClock::elapsedRealtime)
    private val ingress = IngressBudget()
    private val admitted = ConcurrentHashMap.newKeySet<String>()

    /**
     * One boundary for generation transitions, send-queue admission and completion metadata:
     * [generations], [closing], [pending] and the transport's per-address state are only changed
     * while holding [stateLock], so disconnect/reconnect can never interleave with an enqueue
     * or acknowledge a record that was never admitted.
     */
    private val stateLock = Any()
    private val generations = mutableMapOf<String, ConnectionGeneration>()
    private var nextSerial = 0L
    private val closing = mutableSetOf<String>()

    private data class PendingSend(val gen: ConnectionGeneration, val token: Long?)

    /** One queued outbound record and its completion token, in send order per address. */
    private val pending = mutableMapOf<String, ArrayDeque<PendingSend>>()

    /** The fragment passed to `notifyCharacteristicChanged` and awaiting its callback. */
    private data class Flight(val gen: ConnectionGeneration, val serial: Long)
    private val inflight = ConcurrentHashMap<String, Flight>()
    private val flightSerial = java.util.concurrent.atomic.AtomicLong()

    /**
     * A disconnected link left one fragment in flight. The next callback for the address must
     * be consumed as that stale acknowledgement before the replacement generation sends
     * anything, so a delayed old callback can never advance a new fragment queue.
     */
    private data class Quarantine(val flight: Flight, val at: Long)
    private val quarantine = mutableMapOf<String, Quarantine>()

    /**
     * A quarantine that expired without the stale acknowledgement. The next callback for the
     * address is then ambiguous (it may be that delayed ack or the replacement's own ack), so
     * it fails the current link closed instead of ever completing a record. Only a callback
     * consumes the marker; it survives unrelated disconnects.
     */
    private val ambiguous = mutableSetOf<String>()

    /** Idempotent transfer of one GATT ingress slot from the callback to the record's owner. */
    private class BoundedAdmission(
        private val id: String,
        private val budget: IngressBudget,
    ) : RecordAdmission {
        private val released = java.util.concurrent.atomic.AtomicBoolean(false)

        override fun release() {
            if (released.compareAndSet(false, true)) budget.release(id)
        }
    }

    /** Inbound frames accepted but not yet processed; close() must report them as dropped, never silently. */
    private data class QueuedInbound(val id: String, val bytes: Int)
    private val inboundLock = Any()
    private val queuedInbound = LinkedHashMap<Long, QueuedInbound>()
    private var queuedSeq = 0L

    /** Peers whose malformed frame could not be queued (ingress saturated); one queued disconnect each. */
    private val overflowed = ConcurrentHashMap.newKeySet<String>()
    private val devices = mutableMapOf<String, BluetoothDevice>()
    private val subscribed = mutableSetOf<String>()
    private val connectedAt = mutableMapOf<String, Long>()
    private val ready = mutableSetOf<String>()
    private var server: BluetoothGattServer? = null

    @Volatile private var closed = false
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
                if (id in admitted) {
                    // Repeated STATE_CONNECTED for a healthy admitted link: not a capacity refusal, so
                    // it must neither cancel the link nor reset its setup state (#120 M3).
                    return@post
                }
                overflowed.remove(id) // a stale entry must never outlive the link it was raised for
                val serial = ++nextSerial
                if (!transport.connect(id)) {
                    server?.cancelConnection(device)
                } else {
                    devices[id] = device
                    admitted.add(id)
                    if (BuildConfig.DEBUG) SyntheticTransportFixture.telemetry?.connected(id)
                    connectedAt[id] = SystemClock.elapsedRealtime()
                    val gen = ConnectionGeneration(id, serial)
                    synchronized(stateLock) { generations[id] = gen }
                    listener.onConnected(gen)
                }
            }
        }
        override fun onMtuChanged(device: BluetoothDevice, mtu: Int) = post {
            if (BuildConfig.DEBUG && device.address in admitted) SyntheticTransportFixture.telemetry?.mtu(device.address, mtu)
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
            if (BuildConfig.DEBUG) {
                SyntheticTransportFixture.telemetry?.let { telemetry ->
                    val header = if (value.size >= GattTransport.HEADER) {
                        val index = value[0].toInt() and FRAME_INDEX_MASK
                        val final = value[0].toInt() and FRAME_FINAL_FLAG != 0
                        val transaction = value[1].toInt() and BYTE_MASK
                        "index=$index;final=$final;transaction=$transaction"
                    } else {
                        "short"
                    }
                    val hash = if (value.size <= GattTransport.ATTRIBUTE_VALUE_MAX) SyntheticTransportFixture.sha256Hex(value) else null
                    telemetry.inbound(device.address, "fragment_received", header, value.size, hash)
                }
            }
            // Never copy an oversized value. On the write characteristic it is a malformed frame
            // (OTP2-GAT-010) and must discard the peer's partial transactions in arrival order; any other
            // operation is rejected exactly like a normal invalid operation, without touching the transport.
            if (value.size > GattTransport.ATTRIBUTE_VALUE_MAX) {
                val valid = characteristic.uuid == WRITE_UUID && !preparedWrite && !responseNeeded && offset == 0
                if (valid) postMalformed(device) else postUnsupported(device, requestId, responseNeeded)
                return
            }
            val arrival = synchronized(stateLock) { generations[device.address] }
            postValue(device, value, trackReceived = BuildConfig.DEBUG && SyntheticTransportFixture.telemetry != null) { copy, admission ->
                val id = device.address
                val validOperation = characteristic.uuid == WRITE_UUID && !preparedWrite && !responseNeeded && offset == 0
                var handedOff = false
                if (!validOperation || id !in ready || id !in subscribed) {
                    if (BuildConfig.DEBUG) {
                        SyntheticTransportFixture.telemetry?.inbound(
                            id,
                            "fragment_rejected",
                            "invalid_operation_or_not_ready",
                            copy.size,
                            SyntheticTransportFixture.sha256Hex(copy),
                        )
                    }
                    if (responseNeeded) server?.sendResponse(device, requestId, BluetoothGatt.GATT_REQUEST_NOT_SUPPORTED, 0, null)
                } else if (arrival == null || synchronized(stateLock) { generations[id] } != arrival) {
                    // An ended generation's queued fragment must never seed the next link's parser.
                    if (BuildConfig.DEBUG) {
                        SyntheticTransportFixture.telemetry?.inbound(id, "fragment_dropped", "stale_generation", copy.size, null)
                    }
                } else {
                    val result = transport.receive(id, copy)
                    if (BuildConfig.DEBUG) {
                        SyntheticTransportFixture.telemetry?.inbound(
                            id,
                            "fragment_processed",
                            result.diagnostic,
                            copy.size,
                            SyntheticTransportFixture.sha256Hex(copy),
                        )
                    }
                    if (result.disconnect) disconnect(id)
                    result.record?.let { record ->
                        val digest = if (BuildConfig.DEBUG) SyntheticTransportFixture.sha256Hex(record) else null
                        if (BuildConfig.DEBUG) {
                            SyntheticTransportFixture.telemetry?.inbound(id, "record_complete", null, record.size, digest)
                        }
                        // The callee owns a copy plus the ingress admission; the transport's
                        // reassembled buffer is erased here.
                        val owned = record.copyOf()
                        record.fill(0)
                        if (synchronized(stateLock) { generations[id] } == arrival) {
                            listener.onRecord(arrival, owned, admission)
                            handedOff = true
                            if (BuildConfig.DEBUG) {
                                SyntheticTransportFixture.telemetry?.inbound(id, "record_delegated", null, owned.size, digest)
                            }
                        } else {
                            owned.fill(0)
                        }
                    }
                }
                handedOff
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
            postValue(device, value) { copy, _ ->
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
                        if (subscribed.add(device.address) && BuildConfig.DEBUG) {
                            SyntheticTransportFixture.telemetry?.subscribed(device.address)
                            scheduleSyntheticPush(device.address)
                        }
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
                false
            }
        }
        override fun onExecuteWrite(device: BluetoothDevice, requestId: Int, execute: Boolean) = post {
            server?.sendResponse(device, requestId, BluetoothGatt.GATT_REQUEST_NOT_SUPPORTED, 0, null)
            Unit
        }
        override fun onNotificationSent(device: BluetoothDevice, status: Int) {
            // The raw arrival binds the callback to the fragment (and so the generation) that was
            // passed to notifyCharacteristicChanged; the looper hop must not re-resolve either.
            if (BuildConfig.DEBUG) SyntheticTransportFixture.telemetry?.callbackSeen(device.address, status, closed)
            onNotificationSentOnMain(device, status, inflight[device.address])
        }
        private fun onNotificationSentOnMain(device: BluetoothDevice, status: Int, flight: Flight?) = post {
            if (BuildConfig.DEBUG) SyntheticTransportFixture.telemetry?.callback(device.address, status)
            val id = device.address
            // An expired quarantine makes this callback ambiguous: it may be the previous
            // generation's delayed ack, so it must never acknowledge a record. Fail closed.
            if (synchronized(stateLock) { ambiguous.remove(id) }) {
                // Keep this link's flight: remove() then quarantines its outstanding callback, so
                // a delayed ack for the closed replacement can never acknowledge the next
                // generation's fragment.
                disconnect(id)
                return@post
            }
            if (flight == null) {
                // No fenced notification owns this address. If a generation just ended with a
                // fragment in flight, this first callback is that stale acknowledgement: consume
                // it and only then let the replacement generation send. Otherwise a success can
                // acknowledge nothing, while a failure still fails the current link closed.
                val stale = synchronized(stateLock) { quarantine.remove(id) != null }
                if (status != BluetoothGatt.GATT_SUCCESS && !stale) disconnect(id)
                if (stale) pump(id)
                return@post
            }
            var completed: PendingSend? = null
            val acknowledged = synchronized(stateLock) {
                if (inflight[id] !== flight) return@post // a newer fragment owns the callback now
                inflight.remove(id)
                if (generations[id] != flight.gen) return@post // a stale generation's callback
                if (status == BluetoothGatt.GATT_SUCCESS) {
                    if (transport.notificationComplete(id, true)) completed = completedRecordLocked(id)
                    true
                } else {
                    false
                }
            }
            if (!acknowledged) {
                disconnect(id)
                return@post
            }
            completed?.let { record -> if (record.token != null) listener.onSendComplete(record.gen, record.token, true) }
            pump(id)
            finishCloseIfDrained(id)
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
                // A stale acknowledgement that never arrived must not stall a replacement link,
                // but its address must stay fail-closed: the next callback there is ambiguous.
                val released = synchronized(stateLock) {
                    val now = SystemClock.elapsedRealtime()
                    quarantine.filterValues { now - it.at >= QUARANTINE_MILLIS }.keys.toList().also { ids ->
                        ids.forEach {
                            quarantine.remove(it)
                            ambiguous.add(it)
                        }
                    }
                }
                released.forEach(::pump)
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

    /**
     * Debug fixture only (opt-in through [SyntheticTransportFixture.unsolicitedAfterMillis]): one deterministic,
     * unsolicited record per subscription, for background-delivery validation. Evidence keeps hashes only.
     */
    private fun scheduleSyntheticPush(id: String) {
        val delay = SyntheticTransportFixture.unsolicitedAfterMillis ?: return
        val subscribedAt = System.currentTimeMillis()
        handler.postDelayed({
            if (id !in subscribed) return@postDelayed
            val record = SyntheticTransportFixture.unsolicited()
            val queued = synchronized(stateLock) {
                val gen = generations[id]
                if (gen != null && transport.enqueue(id, record)) {
                    pending.getOrPut(id) { ArrayDeque() }.addLast(PendingSend(gen, null))
                    true
                } else {
                    false
                }
            }
            SyntheticTransportFixture.unsolicitedEvidence.add(
                SyntheticTransportFixture.Unsolicited(
                    peer = SyntheticTransportFixture.sha256Hex(id.toByteArray()).take(PEER_HASH_CHARS),
                    subscribedAtMillis = subscribedAt,
                    firedAtMillis = System.currentTimeMillis(),
                    delayMillis = delay,
                    queued = queued,
                    bytes = record.size,
                    sha256 = SyntheticTransportFixture.sha256Hex(record),
                ),
            )
            record.fill(0)
            if (queued) post { pump(id) }
        }, delay)
    }

    private fun pump(id: String) {
        val device = devices[id] ?: return
        val request = synchronized(stateLock) {
            if (id !in subscribed || id in quarantine) return
            val gen = generations[id]
            val fragment = gen?.let { transport.nextFragment(id) }
            if (gen == null || fragment == null) return
            val flight = Flight(gen, flightSerial.incrementAndGet())
            inflight[id] = flight
            flight to fragment
        }
        val code = server?.notifyCharacteristicChanged(device, notify, false, request.second)
        if (BuildConfig.DEBUG && code != null) {
            SyntheticTransportFixture.telemetry?.notifyResult(id, request.second.size, code)
        }
        if (code != BluetoothStatusCodes.SUCCESS) {
            inflight.remove(id, request.first)
            disconnect(id)
        }
    }

    /**
     * Admission only: the record is queued and the pump is posted, so the caller (the session
     * host, possibly off the main looper) gets a synchronous answer and never owns the bytes.
     * Generation validation, enqueue and token insertion happen under one lock, so a disconnect
     * cannot interleave and the pump cannot complete a record before its token is registered.
     */
    override fun send(gen: ConnectionGeneration, bytes: ByteArray, token: Long?): Boolean {
        var refusal: String? = null
        val queued = synchronized(stateLock) {
            when {
                closed || generations[gen.address] != gen || gen.address in closing -> false
                transport.enqueue(gen.address, bytes) -> {
                    pending.getOrPut(gen.address) { ArrayDeque() }.addLast(PendingSend(gen, token))
                    true
                }
                else -> {
                    refusal = transport.enqueueRefusalReason(gen.address, bytes.size)
                    false
                }
            }
        }
        if (BuildConfig.DEBUG) {
            SyntheticTransportFixture.telemetry?.inbound(
                gen.address,
                "reply_queue",
                if (queued) "accepted" else refusal ?: "stale_generation",
                bytes.size,
                SyntheticTransportFixture.sha256Hex(bytes),
            )
        }
        if (!queued) return false
        post { pump(gen.address) }
        return true
    }

    /** Ordered terminal policy: queue flush first, then the cancelled link (design D4). */
    override fun close(gen: ConnectionGeneration) {
        val current = synchronized(stateLock) {
            if (closed || generations[gen.address] != gen) {
                false
            } else {
                closing.add(gen.address)
                true
            }
        }
        if (!current) return
        post {
            if (synchronized(stateLock) { generations[gen.address] } != gen) return@post
            pump(gen.address)
            finishCloseIfDrained(gen.address)
        }
    }

    /** Pops the record whose last fragment was just acknowledged; the caller holds [stateLock]. */
    private fun completedRecordLocked(id: String): PendingSend? {
        val queue = pending[id] ?: return null
        val entry = queue.removeFirstOrNull() ?: return null
        if (queue.isEmpty()) pending.remove(id)
        return if (generations[id] == entry.gen) entry else null
    }

    private fun finishCloseIfDrained(id: String) {
        val drained = synchronized(stateLock) {
            if (id !in closing) {
                false
            } else {
                id !in subscribed || pending[id].isNullOrEmpty()
            }
        }
        if (drained) disconnect(id)
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

    /**
     * Copies [value] under one ingress slot. The [RecordAdmission] is released here unless the
     * block transfers it to a record's owner (returns true), so queued engine work stays bounded.
     */
    private fun postValue(
        device: BluetoothDevice,
        value: ByteArray,
        trackReceived: Boolean = false,
        block: (ByteArray, RecordAdmission) -> Boolean,
    ) {
        val id = device.address
        if (closed) {
            if (BuildConfig.DEBUG && trackReceived) {
                SyntheticTransportFixture.telemetry?.inbound(id, "fragment_dropped", "server_closed", value.size, null)
            }
            return
        }
        if (id !in admitted || id in overflowed) {
            if (BuildConfig.DEBUG) {
                SyntheticTransportFixture.telemetry?.inbound(
                    id,
                    "fragment_dropped",
                    "not_admitted_or_overflowed",
                    value.size,
                    null,
                )
            }
            return
        }
        if (!ingress.acquire(id)) {
            if (BuildConfig.DEBUG) SyntheticTransportFixture.telemetry?.inbound(id, "fragment_dropped", "ingress_limit", value.size, null)
            // The refused frame might be malformed (e.g. 183 bytes at MTU 185, or under 4 bytes); dropping it
            // silently would let an old partial complete across it. Fail the peer closed instead. A dropped
            // VALID frame loses its record and the session anyway (OTP2-ENC-004), so this only turns that
            // silent loss into a bounded disconnect. No payload is copied for a refused admission.
            failClosed(id)
            return
        }
        val admission = BoundedAdmission(id, ingress)
        val copy = value.copyOf()
        val token = if (trackReceived) trackQueuedInbound(id, value.size) else null
        if (trackReceived && token == null) {
            copy.fill(0)
            admission.release()
            return
        }
        post {
            if (token != null) untrackQueuedInbound(token)
            var handedOff = false
            try {
                handedOff = block(copy, admission)
            } finally {
                copy.fill(0)
                if (!handedOff) admission.release()
            }
        }
    }

    private fun trackQueuedInbound(id: String, bytes: Int): Long? = synchronized(inboundLock) {
        if (closed) {
            if (BuildConfig.DEBUG) {
                SyntheticTransportFixture.telemetry?.inbound(id, "fragment_dropped", "server_closed", bytes, null)
            }
            null
        } else {
            val token = ++queuedSeq
            queuedInbound[token] = QueuedInbound(id, bytes)
            token
        }
    }

    private fun untrackQueuedInbound(token: Long) {
        synchronized(inboundLock) { queuedInbound.remove(token) }
    }

    /**
     * Carries no data: one bounded ingress slot per pending discard. If the peer already has the maximum
     * number of callbacks pending the discard cannot be queued, and dropping it would let an old partial
     * complete across the malformed frame; so the peer fails closed with at most ONE queued disconnect,
     * queued ahead of every later frame (later frames from an overflowed peer are refused at arrival).
     */
    private fun postMalformed(device: BluetoothDevice) {
        val id = device.address
        val gen = synchronized(stateLock) { generations[id] }
        if (BuildConfig.DEBUG) SyntheticTransportFixture.telemetry?.inbound(id, "fragment_rejected", "oversized", 0, null)
        if (id !in admitted || id in overflowed) return
        if (!ingress.acquire(id)) {
            failClosed(id)
            return
        }
        post {
            try {
                // An empty frame is below the 4-byte header, i.e. malformed: receive() discards this peer's
                // partial transactions (OTP2-GAT-011) and delivers nothing. A stale generation's discard
                // must not touch the next link's parser.
                if (gen != null && synchronized(stateLock) { generations[id] } == gen) {
                    transport.receive(id, ByteArray(0))
                }
            } finally {
                ingress.release(id)
            }
        }
    }

    /** At most ONE queued disconnect per peer (`overflowed`), ahead of every later frame; later frames are refused at arrival. */
    private fun failClosed(id: String) {
        if (overflowed.add(id)) post { disconnect(id) }
    }

    /** Oversized value on an invalid operation: answer like any invalid operation, never copy the value. */
    private fun postUnsupported(device: BluetoothDevice, requestId: Int, responseNeeded: Boolean) {
        val id = device.address
        if (!responseNeeded || id !in admitted || id in overflowed || !ingress.acquire(id)) return
        post {
            try {
                server?.sendResponse(device, requestId, BluetoothGatt.GATT_REQUEST_NOT_SUPPORTED, 0, null)
            } finally {
                ingress.release(id)
            }
        }
    }

    private fun remove(id: String) {
        if (BuildConfig.DEBUG && id in admitted) SyntheticTransportFixture.telemetry?.disconnected(id)
        val (gen, dropped, _) = synchronized(stateLock) {
            val removed = generations.remove(id)
            closing.remove(id)
            val inFlight = inflight.remove(id)
            if (inFlight != null) quarantine[id] = Quarantine(inFlight, SystemClock.elapsedRealtime())
            transport.disconnect(id)
            Triple(removed, pending.remove(id)?.toList().orEmpty(), inFlight)
        }
        // `flight` is deliberately retained for the quarantine check above; pending tokens are
        // reported below, after the transport state is gone.
        admitted.remove(id)
        overflowed.remove(id)
        devices.remove(id)
        subscribed.remove(id)
        connectedAt.remove(id)
        ready.remove(id)
        // Every queued record of the ended generation is a lost send: report tokened ones.
        dropped.forEach { record ->
            if (record.gen == gen && record.token != null) listener.onSendComplete(record.gen, record.token, false)
        }
        if (gen != null) listener.onDisconnected(gen)
    }
    private fun disconnect(id: String) {
        val device = devices[id]
        remove(id)
        if (device != null) server?.cancelConnection(device)
    }
    fun close() {
        val dropped = if (BuildConfig.DEBUG) {
            synchronized(inboundLock) {
                closed = true
                queuedInbound.values.toList().also { queuedInbound.clear() }
            }
        } else {
            closed = true
            emptyList()
        }
        handler.removeCallbacksAndMessages(null)
        if (BuildConfig.DEBUG) {
            dropped.forEach { queued ->
                SyntheticTransportFixture.telemetry?.inbound(
                    queued.id,
                    "fragment_dropped",
                    "server_closed",
                    queued.bytes,
                    null,
                )
            }
        }
        ingress.close()
        admitted.clear()
        overflowed.clear()
        handler.removeCallbacksAndMessages(null)
        try {
            advertiser?.stopAdvertising(advertising)
        } catch (_: SecurityException) { /* Permission revoked. */ }
        devices.keys.toList().forEach { id ->
            try {
                disconnect(id)
            } catch (_: SecurityException) { /* Local state already erased. */ }
        }
        synchronized(stateLock) {
            pending.clear()
            inflight.clear()
            quarantine.clear()
            ambiguous.clear()
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
        private const val FRAME_INDEX_MASK = 127
        private const val FRAME_FINAL_FLAG = 128
        private const val BYTE_MASK = 255
        val SERVICE_UUID: UUID = UUID.fromString("8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51")
        val WRITE_UUID: UUID = UUID.fromString("8e400001-8f3c-4d1a-b64a-8e3c9b0d2a51")
        val NOTIFY_UUID: UUID = UUID.fromString("8e400002-8f3c-4d1a-b64a-8e3c9b0d2a51")
        val CCCD_UUID: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")
        private const val PEER_HASH_CHARS = 8

        /** How long a disconnected link's last notification may keep the replacement fenced. */
        private const val QUARANTINE_MILLIS = 1_000L
    }
}
