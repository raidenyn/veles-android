@file:Suppress("MagicNumber") // Fixed, public synthetic transport fixture; no OTP dependency.

package me.nagaev.veles.sharing

import android.os.SystemClock
import java.security.MessageDigest
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Debug-only caller: deterministic public bytes for physical transport boundary/isolation tests.
 * Every use sits behind `BuildConfig.DEBUG`, so R8 removes the whole object from release builds.
 */
internal object SyntheticTransportFixture {
    /** Hash-only record of one opt-in unsolicited push; carries no payload bytes and no raw address. */
    data class Unsolicited(
        val peer: String,
        val subscribedAtMillis: Long,
        val firedAtMillis: Long,
        val delayMillis: Long,
        val queued: Boolean,
        val bytes: Int,
        val sha256: String,
    )

    /**
     * Opt-in, set only by the physical instrumentation: wait this long after a central subscribes,
     * then push one [unsolicited] record to it without any request. Null (the default) disables it.
     */
    @Volatile var unsolicitedAfterMillis: Long? = null
    val unsolicitedEvidence = CopyOnWriteArrayList<Unsolicited>()

    /**
     * Opt-in (`-e otp23_transport_evidence true` plus a run id), set only by the physical instrumentation and
     * only ever read behind `BuildConfig.DEBUG`. Null (the default) records and retains nothing.
     */
    @Volatile var telemetry: TransportTelemetry? = null

    fun reply(record: ByteArray): ByteArray? {
        if (record.size !in 4..4096 || !record.copyOfRange(0, 4).contentEquals(byteArrayOf(0x56, 0x45, 0x4c, 1))) return null
        if (record.size > 4) {
            val seed = ((record[4].toInt() and 0xff) - 4) and 0xff
            if ((4 until record.size).any { record[it] != ((it + seed) and 0xff).toByte() }) return null
        }
        return record.copyOf().apply { this[3] = 2 }
    }

    /** Record type 3: `VEL\x03`, then `(index + seed) & 0xff`. A desktop driver recomputes it independently. */
    fun unsolicited(length: Int = 4096, seed: Int = UNSOLICITED_SEED): ByteArray {
        require(length in 4..4096)
        return ByteArray(length) { ((it + seed) and 0xff).toByte() }.also {
            byteArrayOf(0x56, 0x45, 0x4c, 3).copyInto(it)
        }
    }

    fun sha256Hex(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

    const val UNSOLICITED_SEED = 61

    /** Human label for an actual `BluetoothStatusCodes` value; null when this build has no name for it. */
    fun notifyApiLabel(code: Int): String? = when (code) {
        android.bluetooth.BluetoothStatusCodes.SUCCESS -> "SUCCESS"
        android.bluetooth.BluetoothStatusCodes.ERROR_BLUETOOTH_NOT_ENABLED -> "ERROR_BLUETOOTH_NOT_ENABLED"
        android.bluetooth.BluetoothStatusCodes.ERROR_BLUETOOTH_NOT_ALLOWED -> "ERROR_BLUETOOTH_NOT_ALLOWED"
        android.bluetooth.BluetoothStatusCodes.ERROR_MISSING_BLUETOOTH_CONNECT_PERMISSION -> "ERROR_MISSING_BLUETOOTH_CONNECT_PERMISSION"
        android.bluetooth.BluetoothStatusCodes.ERROR_GATT_WRITE_NOT_ALLOWED -> "ERROR_GATT_WRITE_NOT_ALLOWED"
        android.bluetooth.BluetoothStatusCodes.ERROR_GATT_WRITE_REQUEST_BUSY -> "ERROR_GATT_WRITE_REQUEST_BUSY"
        android.bluetooth.BluetoothStatusCodes.ERROR_UNKNOWN -> "ERROR_UNKNOWN"
        else -> null
    }

    /** Human label for an actual `onNotificationSent` GATT status; null when unnamed. */
    fun callbackLabel(status: Int): String? = when (status) {
        android.bluetooth.BluetoothGatt.GATT_SUCCESS -> "GATT_SUCCESS"
        android.bluetooth.BluetoothGatt.GATT_FAILURE -> "GATT_FAILURE"
        else -> null
    }
}

/**
 * Bounded, opt-in, debug-only record of what the Bluetooth stack actually returned for each notification, per
 * peer. Peers are identified by a SHA-256 prefix only (never a MAC); events carry sizes, never bytes. Counters
 * are exact; the event list is capped, and any dropped event sets `truncated`, which forbids a "complete" claim.
 */
internal class TransportTelemetry(
    val runId: String,
    private val epochMillis: () -> Long = System::currentTimeMillis,
    private val elapsedMillis: () -> Long = SystemClock::elapsedRealtime,
) {
    data class Event(
        val seq: Long,
        val kind: String,
        val epochMs: Long,
        val elapsedMs: Long,
        val code: Int?,
        val label: String?,
        val bytes: Int,
        val boundOp: Long?,
    )

    data class Peer(
        val peer: String,
        val notifyCalls: Int,
        val apiSuccess: Int,
        val apiNonZero: Int,
        val attemptedBytes: Long,
        val callbacks: Int,
        val callbackSuccess: Int,
        val callbackNonZero: Int,
        val unboundCallbacks: Int,
        val pendingUnresolved: Int,
        val pendingDropped: Int,
        val callbacksSeen: Int,
        val callbacksSeenNonZero: Int,
        val callbacksAfterClose: Int,
        val connects: Int,
        val disconnects: Int,
        val subscribes: Int,
        val mtu: Int?,
        val truncated: Boolean,
        val events: List<Event>,
    )

    data class Snapshot(val runId: String, val changes: Long, val peersDropped: Int, val truncated: Boolean, val peers: List<Peer>) {
        /**
         * True only when nothing is missing or failing: every API-accepted fragment got a processed callback,
         * no pending fragment was lost, every callback the stack delivered was processed, and no code was non-zero.
         */
        val clean: Boolean
            get() = !truncated &&
                peersDropped == 0 &&
                peers.all {
                    it.pendingUnresolved == 0 &&
                        it.pendingDropped == 0 &&
                        it.unboundCallbacks == 0 &&
                        it.apiNonZero == 0 &&
                        it.callbackNonZero == 0 &&
                        it.callbacksSeenNonZero == 0 &&
                        it.callbacksAfterClose == 0 &&
                        it.callbacksSeen == it.callbacks &&
                        it.callbacks == it.apiSuccess
                }
    }

    private class State(val peer: String) {
        var notifyCalls = 0
        var apiSuccess = 0
        var apiNonZero = 0
        var attemptedBytes = 0L
        var callbacks = 0
        var callbackSuccess = 0
        var callbackNonZero = 0
        var unbound = 0
        var pendingDropped = 0
        var seen = 0
        var seenNonZero = 0
        var afterClose = 0
        var connects = 0
        var disconnects = 0
        var subscribes = 0
        var mtu: Int? = null
        var truncated = false
        val events = ArrayList<Event>()
        val pending = ArrayDeque<Long>()
    }

    private val peers = LinkedHashMap<String, State>()
    private var seq = 0L
    private var changes = 0L
    private var peersDropped = 0

    /** Call when the notify API returned [code] for a fragment of [bytes] bytes. */
    @Synchronized fun notifyResult(address: String, bytes: Int, code: Int) {
        val p = peerFor(address) ?: return
        val op = ++seq
        p.notifyCalls++
        p.attemptedBytes += bytes
        if (code == 0) p.apiSuccess++ else p.apiNonZero++
        // Only an API-accepted fragment can later produce an onNotificationSent callback.
        if (code == 0) {
            if (p.pending.size < MAX_PENDING) p.pending.addLast(op) else p.truncated = true
        }
        add(p, "notify_api", code, SyntheticTransportFixture.notifyApiLabel(code), bytes, op)
    }

    /** Call with the actual `onNotificationSent` status; bound FIFO to the oldest accepted fragment. */
    @Synchronized fun callback(address: String, status: Int) {
        val p = peerFor(address) ?: return
        p.callbacks++
        if (status == 0) p.callbackSuccess++ else p.callbackNonZero++
        val op = p.pending.removeFirstOrNull()
        if (op == null) p.unbound++
        add(p, "notify_callback", status, SyntheticTransportFixture.callbackLabel(status), 0, op)
    }

    /**
     * Raw arrival of an `onNotificationSent` status, called before the main-looper hop so it survives a closed
     * server or flushed queue. [afterClose] marks a status that arrived once the server was closed.
     */
    @Synchronized fun callbackSeen(address: String, status: Int, afterClose: Boolean) {
        val p = peerFor(address) ?: return
        p.seen++
        if (status != 0) p.seenNonZero++
        if (afterClose) {
            p.afterClose++
            add(p, "callback_after_close", status, SyntheticTransportFixture.callbackLabel(status), 0, null)
        }
    }

    @Synchronized fun connected(address: String) {
        val p = peerFor(address) ?: return
        p.connects++
        add(p, "connected", null, null, 0, null)
    }

    @Synchronized fun disconnected(address: String) {
        val p = peerFor(address) ?: return
        p.disconnects++
        // An accepted fragment that never got its callback is lost evidence, never silently forgotten.
        p.pendingDropped += p.pending.size
        p.pending.clear()
        add(p, "disconnected", null, null, 0, null)
    }

    @Synchronized fun mtu(address: String, mtu: Int) {
        val p = peerFor(address) ?: return
        p.mtu = mtu
        add(p, "mtu", mtu, null, 0, null)
    }

    @Synchronized fun subscribed(address: String) {
        val p = peerFor(address) ?: return
        p.subscribes++
        add(p, "subscribed", null, null, 0, null)
    }

    @Synchronized fun snapshot(): Snapshot = Snapshot(
        runId,
        changes,
        peersDropped,
        peersDropped > 0 || peers.values.any { it.truncated },
        peers.values.map {
            Peer(
                it.peer, it.notifyCalls, it.apiSuccess, it.apiNonZero, it.attemptedBytes, it.callbacks, it.callbackSuccess,
                it.callbackNonZero, it.unbound, it.pending.size, it.pendingDropped, it.seen,
                it.seenNonZero, it.afterClose, it.connects, it.disconnects, it.subscribes, it.mtu, it.truncated, it.events.toList(),
            )
        },
    )

    private fun peerFor(address: String): State? {
        val key = SyntheticTransportFixture.sha256Hex(address.toByteArray()).take(PEER_KEY_CHARS)
        peers[key]?.let { return it }
        if (peers.size >= MAX_PEERS) {
            peersDropped++
            changes++
            return null
        }
        return State(key).also { peers[key] = it }
    }

    private fun add(p: State, kind: String, code: Int?, label: String?, bytes: Int, op: Long?) {
        changes++
        if (p.events.size >= MAX_EVENTS_PER_PEER) {
            p.truncated = true
            return
        }
        p.events.add(Event(++seq, kind, epochMillis(), elapsedMillis(), code, label, bytes, op))
    }

    companion object {
        const val MAX_PEERS = 16
        const val MAX_EVENTS_PER_PEER = 4096
        const val MAX_PENDING = 16
        const val PEER_KEY_CHARS = 16
    }
}
