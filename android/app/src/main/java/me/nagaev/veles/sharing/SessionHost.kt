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
import me.nagaev.veles.session.RecordType
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
 * The requester's mutual-removal outcome (design D4, OTP2-REC-013).
 *
 * [REMOVED] means a valid `removal_response` deleted the local record; [KEPT_NOT_CONNECTED]
 * means there was no FULL session of that peer to ask; [KEPT_NO_RESPONSE] means the exchange
 * produced no valid response — a lost response, a disconnect, the deadline or a protocol
 * violation — so the local record is kept and the other endpoint may keep its own.
 */
enum class RemovalResult { REMOVED, KEPT_NOT_CONNECTED, KEPT_NO_RESPONSE }

/**
 * The requester's `removal_response` deadline (design call, not a protocol value; design D4).
 * It is measured on the host's injected clock and checked on the existing 1 000 ms tick.
 */
const val REMOVAL_RESPONSE_TIMEOUT_MS = 10_000L

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
 * The session side of a trust-changing operation (design D1/D2/D3/D6). Selection is by the
 * authenticated installation id only; a label, native handle or platform hint is never an input.
 * [SessionHost] is the production implementation; a conformance runner can bind its own live
 * engine through this surface without a transport.
 */
interface PeerSessions {
    /** True when an authenticated session of [peerId] (the trust installation id, hex) is live. */
    fun hasOpenSession(peerId: String): Boolean

    /** Terminates every generation bound to [peerIdHex] and awaits each one's end. */
    suspend fun closePeer(peerIdHex: String)

    /** Terminates every generation, non-destructively; durable enrollment records are kept. */
    suspend fun closeAll()

    /** Asks the peer's full session to remove this endpoint's record (OTP2-REC-013). */
    suspend fun requestRemoval(peerIdHex: String): RemovalResult

    /**
     * Closes admission for a reset *before* it takes its snapshot (design D6): a connection that
     * arrives later is refused instead of being admitted outside the snapshot, where the reset
     * could delete trust and refresh the identity without terminating it.
     */
    fun beginReset()

    /**
     * Releases the reset barrier only for a completed reset. `completed = false` keeps every
     * later connection refused, because the store may still hold the identity the reset could not
     * discard.
     */
    fun endReset(completed: Boolean)
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
@Suppress("TooManyFunctions") // Lifecycle callbacks, five queries/commands, worker and effect applier.
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
    /** Session-bound proof cleanup when a generation is torn down (OTP-10 plan 9d). */
    private val onSessionClosed: (String) -> Unit = {},
) : ConnectionListener, PeerSessions {
    private class OwnedRecord(val bytes: ByteArray, val admission: RecordAdmission)

    /**
     * One requester removal exchange (design D4). The owner awaits [result]; [deadlineAt] is
     * compared with the injected clock on each existing tick.
     */
    private class RemovalWait(val result: CompletableDeferred<RemovalResult>) {
        /** `Long.MAX_VALUE` until the request is sealed, so an unarmed wait can never expire. */
        @Volatile var deadlineAt = Long.MAX_VALUE
    }

    private sealed interface Step {
        class Inbound(val record: OwnedRecord) : Step
        class Completion(val token: Long, val ok: Boolean) : Step
        data object Tick : Step
        class Application(val type: Int, val body: ByteArray, val result: CompletableDeferred<Boolean>) : Step
        class RemovalStart(val wait: RemovalWait) : Step
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

        /**
         * Set by the first dispatched frame. Only that frame selects the path: later frames
         * always follow the chosen machine or engine (guarded by [mutex], like dispatch).
         */
        var firstFrameSeen = false

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

        /**
         * Completes when [teardownLocked] has erased this generation (design D8). A peer
         * termination awaits it, so "the call returned" means "the generation ended".
         */
        val endedSignal = CompletableDeferred<Unit>()

        /**
         * The requester's in-flight removal exchange (design D4), or null. Written under
         * [registry] by [requestRemoval], [settleRemoval] and [teardownLocked].
         */
        @Volatile var removal: RemovalWait? = null

        /** True when this generation is bound to [peerIdHex]; never by a label or address. */
        fun isBoundTo(peerIdHex: String): Boolean {
            val authenticated = peerId?.let(TrustEncoding::hex)
            return authenticated == peerIdHex || pairing?.boundPeerId() == peerIdHex
        }
    }

    private val registry = Any()
    private val sessions = LinkedHashMap<ConnectionGeneration, Live>()
    private val all = LinkedHashSet<Live>()

    /**
     * The reset-wide admission barrier (design D6). Guarded by [registry] and closed by
     * [beginReset] before a reset snapshots its generations, so no connection can be admitted —
     * and no identity read or handshake can start — while the store is being discarded. It is
     * released only by a completed reset's [endReset].
     */
    private var admitting = true

    override fun onConnected(gen: ConnectionGeneration) {
        val live = Live(gen)
        if (!admit(live)) {
            // A reset owns the store: the connection is never registered, builds no engine (so it
            // reads no identity) and its transport is refused.
            scope.launch { transport.close(gen) }
            return
        }
        live.engine = engineFactory(gen)
        live.tick = scope.launch { runTicks(live) }
        scope.launch { for (step in live.queue) handleStep(live, step) }
    }

    /** Registers [live] only while admission is open; the check and the registration are atomic. */
    private fun admit(live: Live): Boolean = synchronized(registry) {
        if (!admitting) return false
        sessions[live.gen] = live
        all += live
        true
    }

    override fun beginReset() {
        synchronized(registry) { admitting = false }
    }

    override fun endReset(completed: Boolean) {
        synchronized(registry) { admitting = completed }
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
    override fun hasOpenSession(peerId: String): Boolean {
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
     * Asks the peer to remove this endpoint's record (OTP2-REC-013, design D4).
     *
     * The request runs on the serialized queue of the peer's FULL generation: the engine
     * seals `removal_request`, then the removal-pending guard is set on that session only, and
     * the response deadline is armed on the injected clock. The host never deletes anything
     * itself; the removal handler does that inside the validated response's step.
     *
     * [RemovalResult.REMOVED] is returned only when the generation ended through the engine's
     * removal stage. Every other end — a lost response, a disconnect, [REMOVAL_RESPONSE_TIMEOUT_MS]
     * or a protocol violation — returns [RemovalResult.KEPT_NO_RESPONSE]; without a live FULL
     * session of [peerIdHex] the call returns [RemovalResult.KEPT_NOT_CONNECTED]. A second
     * request while one is already awaiting a response returns [RemovalResult.KEPT_NO_RESPONSE]
     * without sending anything.
     */
    @Suppress("ReturnCount") // Not connected, already awaiting, refused admission and the settle are distinct outcomes.
    override suspend fun requestRemoval(peerIdHex: String): RemovalResult {
        val wait = RemovalWait(CompletableDeferred())
        synchronized(registry) {
            val live = sessions.values.firstOrNull {
                !it.ended && it.sessionClass == SessionClass.FULL && it.peerId?.let(TrustEncoding::hex) == peerIdHex
            } ?: return RemovalResult.KEPT_NOT_CONNECTED
            if (live.removal != null) return RemovalResult.KEPT_NO_RESPONSE
            // Registered before the step can run, so a disconnect or teardown always settles it.
            live.removal = wait
            if (!live.queue.trySend(Step.RemovalStart(wait)).isSuccess) {
                live.removal = null
                return RemovalResult.KEPT_NO_RESPONSE
            }
        }
        return wait.result.await()
    }

    /**
     * Seals the requester's `removal_request` under the generation's mutex and arms the guard
     * and the deadline in the same serialized step. The request is sealed before the guard is
     * set, so the request itself is never rejected by it.
     */
    @Suppress("TooGenericExceptionCaught") // An engine rejection fails the generation like any other step.
    private suspend fun startRemovalLocked(live: Live, wait: RemovalWait) {
        val engine = live.engine
        if (!isCurrent(live) || engine == null || !engine.isOpen) {
            settleRemoval(live, RemovalResult.KEPT_NO_RESPONSE)
            return
        }
        try {
            val effects = engine.sendApplication(RecordType.REMOVAL_REQUEST.wire, ByteArray(0))
            engine.setRemovalPending(true)
            wait.deadlineAt = clock() + REMOVAL_RESPONSE_TIMEOUT_MS
            apply(live, effects)
        } catch (cancellation: CancellationException) {
            throw cancellation
        } catch (_: Exception) {
            failedLocked(live)
        }
    }

    /**
     * Design D4 deadline: compared with the injected clock on each existing 1 000 ms tick,
     * never by a sleep. An expired exchange keeps the record and fails the session.
     */
    private suspend fun expireRemoval(live: Live): Boolean {
        val wait = synchronized(registry) { live.removal } ?: return false
        if (clock() < wait.deadlineAt) return false
        settleRemoval(live, RemovalResult.KEPT_NO_RESPONSE)
        failedLocked(live)
        return true
    }

    /**
     * Settles an exchange from a sealed terminal effect that can no longer be applied because
     * the generation ended first (design D4). The engine reports the removal stage only after
     * the handler returned, so the stage proves the local record was deleted even though the
     * terminal pair never reached the transport; any other terminal stage produced no valid
     * response. Nothing in [effects] is used for anything else.
     */
    private fun settleTerminalFrom(live: Live, effects: List<SessionEffect>, from: Int) {
        val reason = effects.drop(from).filterIsInstance<SessionEffect.Terminated>().lastOrNull()?.reason ?: return
        settleRemoval(live, settlementOf(reason))
    }

    /** The removal outcome of a terminal stage: only a removal stage deleted the record. */
    private fun settlementOf(reason: String): RemovalResult = when (reason) {
        PhoneSessionEngine.STAGE_REMOVAL -> RemovalResult.REMOVED
        else -> RemovalResult.KEPT_NO_RESPONSE
    }

    /**
     * Settles the generation's in-flight removal exchange exactly once (design D4): the first
     * outcome wins, so a disconnect racing a delivered response keeps whichever ended first.
     */
    private fun settleRemoval(live: Live, outcome: RemovalResult) {
        val wait = synchronized(registry) { live.removal.also { live.removal = null } } ?: return
        wait.result.complete(outcome)
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

    /**
     * Explicit user/debug cancel of every live enrollment connection: each pairing machine
     * deletes the pending record it created, then the generation is torn down and its
     * transport closed. Unlike [shutdown], this is the only path that deletes durable
     * incomplete records; service shutdown keeps them for recovery. Safe when no generation
     * has a pairing machine.
     */
    suspend fun cancelPairings() {
        val lives = synchronized(registry) {
            sessions.values.filter { !it.ended && it.pairing != null }
        }
        lives.forEach { live ->
            live.mutex.withLock {
                if (!isCurrent(live)) return@withLock
                val pairing = live.pairing ?: return@withLock
                try {
                    applyPairing(live, pairing.cancel())
                } catch (cancellation: CancellationException) {
                    throw cancellation
                } catch (_: Exception) {
                    failedLocked(live)
                }
                // The machine returns a disconnect, which [applyPairing] already applies. A
                // machine that returns no terminal item is still torn down here.
                if (isCurrent(live)) {
                    teardownLocked(live)
                    transport.close(live.gen)
                }
            }
        }
    }

    private fun enqueue(live: Live, step: Step): Boolean = !live.ended && live.queue.trySend(step).isSuccess

    /**
     * Terminates every generation bound to [peerIdHex] and returns only after each of them has
     * ended (design D3). A generation is selected by its authenticated peer id or by the peer
     * its live enrollment connection is bound to; connections of other peers, enrollments not
     * yet bound to a peer, and address-based reconnects are never selected, and a label, native
     * handle or hint is never an input.
     *
     * Each match runs the teardown and the transport close under that generation's own mutex,
     * so this waits behind an in-flight step — including a handler's reply sends and close
     * effects — and the reply is delivered first. A handler must never call this on its own
     * generation: the call would wait for the step it is running inside.
     */
    override suspend fun closePeer(peerIdHex: String) {
        val lives = synchronized(registry) { all.filter { it.isBoundTo(peerIdHex) } }
        endAll(lives)
    }

    /**
     * Non-destructive teardown of every generation (design D6): keys are erased and the
     * transport is closed, while every durable enrollment record and pairing state is kept.
     * Distinct from [cancelPairings], the destructive path that deletes the incomplete records
     * of live enrollments; fail-closed recovery and identity reset use this one.
     */
    override suspend fun closeAll() {
        val lives = synchronized(registry) { all.toList() }
        endAll(lives)
    }

    /**
     * Ends every generation in [lives], continuing across failures (design D2): a best-effort
     * transport disconnect that fails for one generation must never abort the logical termination
     * of another, so every selected generation is torn down before the first failure is reported,
     * with the remaining failures attached to it.
     */
    @Suppress("TooGenericExceptionCaught") // A disconnect failure of one generation never aborts another.
    private suspend fun endAll(lives: List<Live>) {
        var failure: Exception? = null
        for (live in lives) {
            try {
                endGeneration(live)
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (error: Exception) {
                if (failure == null) {
                    failure = error
                } else {
                    failure.addSuppressed(error)
                }
            }
        }
        if (failure != null) throw failure
    }

    /**
     * Tears [live] down and closes its transport when it is still admitted, then awaits the end
     * signal: the teardown is authoritative, the disconnect is best-effort and its failure is
     * reported only after the logical termination is complete (design D2).
     */
    private suspend fun endGeneration(live: Live) {
        val failure = live.mutex.withLock {
            val admitted = isCurrent(live)
            teardownLocked(live)
            if (admitted) closeTransport(live) else null
        }
        live.endedSignal.await()
        if (failure != null) throw failure
    }

    /** The best-effort disconnect; a failure is reported once the generation has ended. */
    @Suppress("TooGenericExceptionCaught") // The transport is not allowed to keep authorization alive.
    private fun closeTransport(live: Live): Exception? = try {
        transport.close(live.gen)
        null
    } catch (error: Exception) {
        error
    }

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
                    if (expireRemoval(live)) return@withLock
                    val pairing = live.pairing
                    if (pairing != null) {
                        runPairing(live) { pairing.onTick() }
                    } else {
                        val engine = live.engine
                        if (engine != null) runStep(live) { engine.onTick() }
                    }
                }
            }
            is Step.RemovalStart -> live.mutex.withLock { startRemovalLocked(live, step.wait) }
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
                // Design D4: a sealed terminal outcome is logical state, not transport admission.
                // A disconnect can end the generation while a removal handler is still deleting;
                // the deletion that already completed must still settle the exchange, while no
                // send may reach the transport of an ended generation.
                settleTerminalFrom(live, effects, index)
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
                is SessionEffect.Terminated -> settleRemoval(live, settlementOf(effect.reason))
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
     *
     * Only the first frame of a generation selects the path. Re-checking later frames would
     * read a protected record's version/schema bytes (`01 01`) as an envelope length and
     * hand an authenticated reconnect generation to the pairing machine, and would let a
     * mid-handshake pairing frame spend the pairing budgets on reconnect traffic.
     */
    private suspend fun dispatchInbound(live: Live, bytes: ByteArray) {
        if (!live.firstFrameSeen) {
            live.firstFrameSeen = true
            if (isPairingFrame(bytes)) {
                live.engine?.teardown()
                live.engine = null
                val machine = pairingFactory(live.gen)
                live.pairing = machine
                runPairing(live) { machine.onFrame(bytes) }
                return
            }
        }
        val pairing = live.pairing
        if (pairing != null) {
            runPairing(live) { pairing.onFrame(bytes) }
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
        // Design D4: an exchange still awaiting a response keeps the record, including when its
        // start step never ran (shutdown, cancellation).
        settleRemoval(live, RemovalResult.KEPT_NO_RESPONSE)
        live.pairing?.onDisconnect()
        live.pairing = null
        live.engine?.teardown()
        live.engine = null
        live.sessionClass = null
        live.peerId?.fill(0)
        live.peerId = null
        live.sessionIdPrefix?.let { onSessionClosed(TrustEncoding.hex(it)) }
        live.sessionIdPrefix?.fill(0)
        live.sessionIdPrefix = null
        synchronized(registry) {
            if (sessions[live.gen] === live) sessions.remove(live.gen)
            all.remove(live)
        }
        // Design D8: a peer termination awaits this signal, so it completes only after the
        // generation's keys, records and host state are all gone.
        live.endedSignal.complete(Unit)
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
