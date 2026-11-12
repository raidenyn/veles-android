package me.nagaev.veles.sharing

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import me.nagaev.veles.pairing.Outgoing
import me.nagaev.veles.pairing.PairingConnection
import me.nagaev.veles.pairing.RejectingPairingConnection
import me.nagaev.veles.session.PhoneSessionEngine
import me.nagaev.veles.session.SessionClass
import me.nagaev.veles.session.SessionEffect
import me.nagaev.veles.session.SessionReject
import me.nagaev.veles.session.UnpromptedApplication
import me.nagaev.veles.trust.InstallationTrustRepository
import me.nagaev.veles.trust.TrustEncoding
import me.nagaev.veles.trust.TrustException

/**
 * One admitted link. An address alone is not an identity across reconnects, so [serial]
 * increases monotonically on every accepted `STATE_CONNECTED` (design D4).
 */
data class ConnectionGeneration(val address: String, val serial: Long)

/**
 * One authenticated generation with its fixed class and 8-byte session id prefix. The prefix
 * is a copy owned by the caller; the host erases its own copy on teardown.
 */
class AuthenticatedSession(
    val generation: ConnectionGeneration,
    val sessionClass: SessionClass,
    val peerId: String,
    val sessionIdPrefix: ByteArray,
)

/**
 * Bounded admission of exactly one inbound record. The callee owns it until it erases the
 * record ([ConnectionListener.onRecord]); releasing earlier would let the GATT ingress budget
 * stop bounding queued records while a signer or handler is suspended.
 */
fun interface RecordAdmission {
    fun release()
}

/** Lifecycle and record callbacks, scoped to a connection generation. */
interface ConnectionListener {
    fun onConnected(gen: ConnectionGeneration)

    /**
     * The callee owns [bytes] and [admission] and must zero and release them when the record
     * has been processed (or when the generation ends).
     */
    fun onRecord(gen: ConnectionGeneration, bytes: ByteArray, admission: RecordAdmission)

    fun onDisconnected(gen: ConnectionGeneration)

    fun onSendComplete(gen: ConnectionGeneration, token: Long, ok: Boolean)
}

/**
 * Byte transport for session effects. [send] reports enqueue admission only; [close] flushes
 * the send queue ahead of the disconnect so a terminal frame can never be cut off (design D4).
 */
interface SessionTransport {
    fun send(gen: ConnectionGeneration, bytes: ByteArray, token: Long?): Boolean

    fun close(gen: ConnectionGeneration)
}

/**
 * OTP-06 plaintext echo switch. The echo may only be selected while this is true, and it is
 * mutually exclusive with [SessionHost]: the sharing service installs exactly one listener.
 */
internal object TransportMode {
    @Volatile var instrumentationEcho: Boolean = false
}

/**
 * Android transport adapter (design D4): one engine, one [Mutex] and one bounded FIFO worker
 * queue per generation. Engine work is enqueued synchronously at callback ingress, so records
 * reach the engine in arrival order even on a multi-threaded dispatcher, and every engine call
 * for a generation runs under that generation's mutex.
 *
 * A result that arrives after the generation ended is dropped without touching the transport.
 * Inbound copies and their GATT admissions are erased after processing, or by [shutdown] (and
 * link loss) even when an engine call is still suspended.
 *
 * The host never writes trust except [InstallationTrustRepository.markAuthenticated] on an
 * [SessionEffect.Authenticated] effect, still under the mutex, after re-checking that the
 * generation is current and the engine is open.
 */
@Suppress("TooManyFunctions") // Lifecycle callbacks, three public queries, worker and effect applier.
class SessionHost(
    private val scope: CoroutineScope,
    private val transport: SessionTransport,
    private val engineFactory: (ConnectionGeneration) -> PhoneSessionEngine,
    private val trust: InstallationTrustRepository,
    private val clock: () -> Long,
    /**
     * The pairing path for a connection whose first frame is `pairing_request`. The default
     * rejects that frame generically, so every non-pairing caller (tests, the debug probe)
     * gets the unchanged reconnect behavior without production wiring.
     */
    private val pairingFactory: (ConnectionGeneration) -> PairingConnection = { RejectingPairingConnection() },
) : ConnectionListener {
    private class OwnedRecord(val bytes: ByteArray, val admission: RecordAdmission)

    private sealed interface Step {
        class Inbound(val record: OwnedRecord) : Step
        class Completion(val token: Long, val ok: Boolean) : Step
        data object Tick : Step
        class Application(val type: Int, val body: ByteArray, val result: CompletableDeferred<Boolean>) : Step
        data object Teardown : Step
    }

    private class Live(val gen: ConnectionGeneration) {
        val mutex = Mutex()

        /**
         * The reconnect engine, created eagerly so an idle connection still has the
         * handshake deadline. The first `pairing_request` tears it down and switches this
         * generation to [pairing].
         */
        @Volatile var engine: PhoneSessionEngine? = null

        /** The pairing machine, created only for a generation whose first frame is `0x01`. */
        @Volatile var pairing: PairingConnection? = null

        /** Bounded FIFO: inbound is capped by the GATT ingress budget; the rest is slack. */
        val queue = Channel<Step>(QUEUE_CAPACITY)

        @Volatile var ended = false

        @Volatile var tick: Job? = null

        /** At most one tick step may be queued, so a saturated worker cannot exhaust the queue. */
        val tickQueued = java.util.concurrent.atomic.AtomicBoolean(false)

        /** Records handed in through [onRecord] and not yet erased; guarded by [registry]. */
        val inbound = LinkedHashSet<OwnedRecord>()

        /** Senders waiting on a queued application step; all of them are failed on teardown. */
        val applications = LinkedHashSet<Step.Application>()

        /** Set once the engine reports authentication; volatile so the probe can query it safely. */
        @Volatile var sessionClass: SessionClass? = null

        @Volatile var peerId: ByteArray? = null

        @Volatile var sessionIdPrefix: ByteArray? = null
    }

    private val registry = Any()
    private val sessions = LinkedHashMap<ConnectionGeneration, Live>()
    private val all = LinkedHashSet<Live>()

    override fun onConnected(gen: ConnectionGeneration) {
        val live = Live(gen)
        live.engine = engineFactory(gen)
        synchronized(registry) {
            sessions[gen] = live
            all += live
        }
        live.tick = scope.launch { runTicks(live) }
        scope.launch { for (step in live.queue) handleStep(live, step) }
    }

    override fun onRecord(gen: ConnectionGeneration, bytes: ByteArray, admission: RecordAdmission) {
        val record = OwnedRecord(bytes, admission)
        val live = synchronized(registry) { sessions[gen]?.also { it.inbound += record } }
        if (live == null) {
            erase(record)
            return
        }
        if (!enqueue(live, Step.Inbound(record))) {
            synchronized(registry) { live.inbound -= record }
            erase(record)
            onDisconnected(gen)
        }
    }

    override fun onSendComplete(gen: ConnectionGeneration, token: Long, ok: Boolean) {
        val live = synchronized(registry) { sessions[gen] } ?: return
        enqueue(live, Step.Completion(token, ok))
    }

    override fun onDisconnected(gen: ConnectionGeneration) {
        val live = synchronized(registry) { sessions.remove(gen) } ?: return
        live.ended = true
        live.tick?.cancel()
        // Teardown is just another FIFO step so it runs after the in-flight engine call and
        // before any later one. shutdown() is the fallback when the worker cannot run at all.
        if (!live.queue.trySend(Step.Teardown).isSuccess) {
            scope.launch { live.mutex.withLock { teardownLocked(live) } }
        }
        live.queue.close()
    }

    /** Generations that fixed the FULL class and are still open. */
    fun openFullSessions(): List<ConnectionGeneration> = synchronized(registry) {
        sessions.values
            .filter { !it.ended && it.sessionClass == SessionClass.FULL }
            .map { it.gen }
    }

    /**
     * Authenticated sessions with their fixed class and a copied session id prefix. A
     * generation is listed only after the authentication gate opened, never during its
     * handshake.
     */
    fun authenticatedSessions(): List<AuthenticatedSession> = synchronized(registry) {
        sessions.values.mapNotNull { live ->
            // `ended` is set before teardown removes the generation; never offer it as a
            // destination while shutdown is draining.
            if (live.ended) return@mapNotNull null
            val sessionClass = live.sessionClass ?: return@mapNotNull null
            val prefix = live.sessionIdPrefix ?: return@mapNotNull null
            AuthenticatedSession(
                generation = live.gen,
                sessionClass = sessionClass,
                peerId = live.peerId?.let(TrustEncoding::hex) ?: "",
                sessionIdPrefix = prefix.copyOf(),
            )
        }
    }

    /** True when an open session of [peerId] (the trust installation id, hex) is live. */
    fun hasOpenSession(peerId: String): Boolean {
        val raw = runCatching { TrustEncoding.bytes(peerId) }.getOrNull() ?: return false
        return synchronized(registry) {
            sessions.values.any { !it.ended && it.sessionClass != null && it.peerId?.contentEquals(raw) == true }
        }
    }

    /**
     * Sends one application record on [gen]. False when the generation is gone, the engine is
     * closed, the record is not authorized, or the transport refused the enqueue.
     */
    suspend fun sendApplication(gen: ConnectionGeneration, type: Int, body: ByteArray): Boolean {
        val live = synchronized(registry) { sessions[gen] } ?: return false
        val step = Step.Application(type, body, CompletableDeferred())
        // Non-suspending admission and registration are one critical section: a full or ended
        // queue fails the caller immediately (never suspends on admission), and every accepted
        // caller is registered before the worker can run, so teardown always completes it.
        val accepted = synchronized(registry) {
            if (live.ended) {
                false
            } else {
                live.applications += step
                if (live.queue.trySend(step).isSuccess) {
                    true
                } else {
                    live.applications -= step
                    false
                }
            }
        }
        if (!accepted) return false
        return try {
            step.result.await()
        } finally {
            synchronized(registry) { live.applications -= step }
        }
    }

    /**
     * Erases every engine and every record that is still owned by the host, including records
     * queued behind a suspended engine call. Safe to call before cancelling [scope]: the
     * worker is not required, only the generation mutex. A call that never returns is timed
     * out and the erasure is applied anyway; the generation is ended and cannot admit new work.
     */
    suspend fun shutdown() = withContext(NonCancellable) {
        val lives = synchronized(registry) { all.toList() }
        lives.forEach { live ->
            live.ended = true
            live.tick?.cancel()
            live.queue.close()
        }
        val remaining = LinkedHashSet(lives)
        withTimeoutOrNull(SHUTDOWN_TIMEOUT_MS) {
            lives.forEach { live ->
                live.mutex.withLock { teardownLocked(live) }
                remaining.remove(live)
            }
        }
        // The engine call never returned: the generation is ended and cannot admit new work,
        // so this last-resort erasure is the only remaining option.
        remaining.forEach { teardownLocked(it) }
    }

    private fun enqueue(live: Live, step: Step): Boolean = !live.ended && live.queue.trySend(step).isSuccess

    private suspend fun runTicks(live: Live) {
        var last = clock()
        while (true) {
            delay(TICK_MILLIS)
            val now = clock()
            // A single tick per elapsed second even after a long sleep; the engine's own
            // deadlines decide whether that first tick is already terminal.
            if (now <= last) continue
            last = now
            if (live.ended) return
            enqueueTick(live)
        }
    }

    /**
     * Coalesces ticks: at most one tick waits behind a suspended engine step, so a slow handler
     * can never fill the queue and the producer keeps running for later deadlines.
     */
    private fun enqueueTick(live: Live) {
        if (!live.tickQueued.compareAndSet(false, true)) return
        if (!enqueue(live, Step.Tick)) live.tickQueued.set(false)
    }

    private suspend fun handleStep(live: Live, step: Step) {
        when (step) {
            is Step.Inbound -> live.mutex.withLock {
                try {
                    if (isCurrent(live)) dispatchInbound(live, step.record.bytes)
                } finally {
                    eraseStep(live, step.record)
                }
            }
            is Step.Completion -> live.mutex.withLock {
                val engine = live.engine
                if (isCurrent(live) && engine != null) {
                    runStep(live) { engine.onSendComplete(step.token, step.ok) }
                }
            }
            Step.Tick -> {
                live.tickQueued.set(false)
                live.mutex.withLock {
                    if (!isCurrent(live)) return@withLock
                    val pairing = live.pairing
                    if (pairing != null) {
                        runPairing(live) { pairing.onTick() }
                    } else {
                        val engine = live.engine
                        if (engine != null) runStep(live) { engine.onTick() }
                    }
                }
            }
            is Step.Application -> {
                val result = live.mutex.withLock {
                    val engine = live.engine
                    if (!isCurrent(live) || engine == null || !engine.isOpen) {
                        false
                    } else {
                        try {
                            apply(live, engine.sendApplication(step.type, step.body))
                        } catch (cancellation: CancellationException) {
                            throw cancellation
                        } catch (_: Exception) {
                            failedLocked(live)
                            false
                        }
                    }
                }
                synchronized(registry) { live.applications -= step }
                step.result.complete(result)
            }
            Step.Teardown -> live.mutex.withLock { teardownLocked(live) }
        }
    }

    private suspend fun runStep(live: Live, call: suspend () -> List<SessionEffect>) {
        try {
            apply(live, call())
        } catch (cancellation: CancellationException) {
            throw cancellation
        } catch (_: SessionReject) {
            failedLocked(live)
        } catch (_: Exception) {
            failedLocked(live)
        }
    }

    @Suppress("ReturnCount") // Each branch is a distinct terminal outcome for the generation.
    private suspend fun apply(live: Live, effects: List<SessionEffect>): Boolean {
        var sent = false
        for ((index, effect) in effects.withIndex()) {
            if (!isCurrent(live)) {
                eraseSends(effects, index)
                return false
            }
            when (effect) {
                is SessionEffect.Send -> when (deliver(live, effect)) {
                    null -> {
                        // The generation ended mid-effect: drop the rest, never touching the transport.
                        eraseSends(effects, index)
                        return false
                    }
                    false -> {
                        failedLocked(live)
                        return false
                    }
                    true -> sent = true
                }
                is SessionEffect.Authenticated -> {
                    if (!markAuthenticated(live, effect)) {
                        eraseApplications(effect.unprompted, 0)
                        return false
                    }
                    // OTP2-STA-010b steps 0/5: the owner sends what the session owes only after
                    // it handled the gate, never from inside the handler's context hook.
                    if (!sendUnprompted(live, effect.unprompted)) return false
                }
                is SessionEffect.Terminated -> Unit
                is SessionEffect.Deliver -> Unit // OTP-09 has no application consumer; OTP-10 adds one.
                SessionEffect.Close -> {
                    teardownLocked(live)
                    transport.close(live.gen)
                }
            }
        }
        return sent
    }

    /**
     * Routes the first frame: a `pairing_request` (`0x01`) tears down the eager reconnect
     * engine and hands the generation to the pairing machine; every other first frame
     * stays on the reconnect engine, whose pre-auth frame set is therefore unchanged.
     */
    private suspend fun dispatchInbound(live: Live, bytes: ByteArray) {
        val pairing = live.pairing
        if (pairing != null) {
            runPairing(live) { pairing.onFrame(bytes) }
            return
        }
        if (isPairingFrame(bytes)) {
            live.engine?.teardown()
            live.engine = null
            val machine = pairingFactory(live.gen)
            live.pairing = machine
            runPairing(live) { machine.onFrame(bytes) }
            return
        }
        val engine = live.engine ?: return
        runStep(live) { engine.onInbound(bytes) }
    }

    /**
     * Selects the pairing path for the first frame of a generation.
     *
     * `0x02`-`0x05` are unambiguous pairing frames: a protected record starts with the
     * protocol version `1` and a reconnect frame with `0x06`/`0x07`/`0x0F`, never `2`-`5`.
     * They are routed to the machine even with a broken envelope so it can count them as a
     * malformed pairing connection.
     *
     * `0x01` is shared: `pairing_request` and a protected record's first byte are both
     * `1`. The envelope length must match, so a malformed `0x01` frame (a bad length) stays
     * on the reconnect engine and gets the generic reject without spending any pairing
     * budget — reconnect traffic MUST NOT share the pairing budgets (limits.json). That is
     * the accepted boundary: the machine never sees such a frame.
     */
    private fun isPairingFrame(bytes: ByteArray): Boolean {
        if (bytes.isEmpty()) return false
        val type = bytes[0].toInt() and U8_MASK
        // The type is enough for `0x02`-`0x05`: even a one-byte frame is pairing traffic
        // and must reach the machine so it counts as a malformed pairing connection.
        if (type != PAIRING_REQUEST_TYPE) return type in PAIRING_FIRST_FRAME_MIN..PAIRING_FIRST_FRAME_MAX
        if (bytes.size < PAIRING_FRAME_HEADER) return false
        val length = ((bytes[1].toInt() and U8_MASK) shl BITS_PER_BYTE) or (bytes[2].toInt() and U8_MASK)
        return length == bytes.size - PAIRING_FRAME_HEADER
    }

    private suspend fun runPairing(live: Live, call: suspend () -> List<Outgoing>) {
        try {
            applyPairing(live, call())
        } catch (cancellation: CancellationException) {
            throw cancellation
        } catch (_: Exception) {
            failedLocked(live)
        }
    }

    @Suppress("ReturnCount") // Each branch is a distinct terminal outcome for the generation.
    private suspend fun applyPairing(live: Live, outgoing: List<Outgoing>): Boolean {
        var sent = false
        for ((index, item) in outgoing.withIndex()) {
            if (!isCurrent(live)) {
                eraseOutgoing(outgoing, index)
                return false
            }
            when (item) {
                is Outgoing.Frame -> when (sendPairingFrame(live, item)) {
                    null -> {
                        // The generation ended mid-effect: drop the rest, never touching the transport.
                        eraseOutgoing(outgoing, index + 1)
                        return false
                    }
                    false -> {
                        failedLocked(live)
                        return false
                    }
                    true -> sent = true
                }
                Outgoing.Disconnect -> {
                    // The machine already closed its handle; the host ends the generation.
                    teardownLocked(live)
                    transport.close(live.gen)
                    return sent
                }
            }
        }
        return sent
    }

    /** Null when the generation already ended; otherwise the transport's admission answer. */
    private fun sendPairingFrame(live: Live, frame: Outgoing.Frame): Boolean? {
        val bytes = frame.bytes
        return try {
            if (isCurrent(live)) transport.send(live.gen, bytes, null) else null
        } finally {
            bytes.fill(0)
        }
    }

    /** Erases pairing frames that will never reach the transport. */
    private fun eraseOutgoing(outgoing: List<Outgoing>, from: Int) {
        for (index in from until outgoing.size) {
            (outgoing[index] as? Outgoing.Frame)?.bytes?.fill(0)
        }
    }

    /** Null when the generation already ended; otherwise the transport's admission answer. */
    private fun deliver(live: Live, send: SessionEffect.Send): Boolean? {
        val bytes = send.bytes
        return try {
            if (isCurrent(live)) transport.send(live.gen, bytes, send.completionToken) else null
        } finally {
            bytes.fill(0)
        }
    }

    /** Erases engine-owned records that will never reach the transport. */
    private fun eraseSends(effects: List<SessionEffect>, from: Int) {
        for (index in from until effects.size) {
            (effects[index] as? SessionEffect.Send)?.bytes?.fill(0)
        }
    }

    /**
     * Sends the records the freshly opened session owes (OTP2-STA-010b steps 0/5). The caller
     * holds [Live.mutex]; the engine's `sendApplication` is the same path a user send takes, so
     * authorization and sequence rules apply unchanged. Bodies are erased after each attempt.
     */
    @Suppress("ReturnCount") // Each guard is a distinct terminal outcome for the generation.
    private suspend fun sendUnprompted(
        live: Live,
        applications: List<UnpromptedApplication>,
    ): Boolean {
        val engine = live.engine ?: run {
            eraseApplications(applications, 0)
            return true
        }
        for ((index, application) in applications.withIndex()) {
            try {
                if (!isCurrent(live) || !engine.isOpen) {
                    eraseApplications(applications, index)
                    return false
                }
                val effects = engine.sendApplication(application.type, application.body)
                if (effects.isEmpty()) continue
                if (!apply(live, effects)) return false
            } catch (_: SessionReject) {
                failedLocked(live)
                return false
            } catch (_: Exception) {
                failedLocked(live)
                return false
            } finally {
                application.body.fill(0)
            }
        }
        return true
    }

    private fun eraseApplications(applications: List<UnpromptedApplication>, from: Int) {
        for (index in from until applications.size) applications[index].body.fill(0)
    }

    private suspend fun markAuthenticated(live: Live, effect: SessionEffect.Authenticated): Boolean {
        try {
            val engine = live.engine
            if (!isCurrent(live) || engine == null || !engine.isOpen) return false
            live.sessionClass = effect.sessionClass
            live.peerId = effect.peerId.copyOf()
            live.sessionIdPrefix = effect.sessionIdPrefix.copyOf()
            trust.markAuthenticated(TrustEncoding.hex(effect.peerId))
            return true
        } catch (_: TrustException) {
            // The peer was deleted or the trust store failed between lookup and the gate: fail closed.
            failedLocked(live)
            return false
        } finally {
            effect.peerId.fill(0)
            effect.sessionIdPrefix.fill(0)
        }
    }

    /** Terminal failure; the caller already holds [Live.mutex]. */
    private suspend fun failedLocked(live: Live) {
        teardownLocked(live)
        transport.close(live.gen)
    }

    private fun erase(record: OwnedRecord) {
        record.bytes.fill(0)
        record.admission.release()
    }

    /** Erases one processed record; the caller already holds [Live.mutex]. */
    private fun eraseStep(live: Live, record: OwnedRecord) {
        // Teardown may have erased it already; the admission is released exactly once, by whoever
        // removes the record from the owned set.
        if (synchronized(registry) { live.inbound.remove(record) }) erase(record)
    }

    /**
     * Erases the engine, every registered record and all host state; the caller holds
     * [Live.mutex] and the operation is idempotent. Buffered worker steps reference the same
     * records, so their later erase is a no-op.
     */
    private suspend fun teardownLocked(live: Live) {
        live.ended = true
        live.tick?.cancel()
        live.queue.close()
        val records = synchronized(registry) { live.inbound.toList().also { live.inbound.clear() } }
        records.forEach(::erase)
        // Every waiting sendApplication caller gets a deterministic failure, even when the
        // worker never processes its queued step (shutdown, cancellation).
        val applications = synchronized(registry) { live.applications.toList().also { live.applications.clear() } }
        applications.forEach { it.result.complete(false) }
        live.pairing?.onDisconnect()
        live.pairing = null
        live.engine?.teardown()
        live.engine = null
        live.sessionClass = null
        live.peerId?.fill(0)
        live.peerId = null
        live.sessionIdPrefix?.fill(0)
        live.sessionIdPrefix = null
        synchronized(registry) {
            if (sessions[live.gen] === live) sessions.remove(live.gen)
            all.remove(live)
        }
    }

    private fun isCurrent(live: Live): Boolean = !live.ended && synchronized(registry) { sessions[live.gen] === live }

    private companion object {
        const val TICK_MILLIS = 1_000L
        const val QUEUE_CAPACITY = 32
        const val SHUTDOWN_TIMEOUT_MS = 2_000L
        const val U8_MASK = 0xFF
        const val BITS_PER_BYTE = 8
        const val PAIRING_REQUEST_TYPE = 0x01
        const val PAIRING_FRAME_HEADER = 3
        const val PAIRING_FIRST_FRAME_MIN = 0x02
        const val PAIRING_FIRST_FRAME_MAX = 0x05
    }
}
