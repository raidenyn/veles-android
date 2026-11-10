package me.nagaev.veles.sharing

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import me.nagaev.veles.session.PhoneSessionEngine
import me.nagaev.veles.session.SessionClass
import me.nagaev.veles.session.SessionEffect
import me.nagaev.veles.session.SessionReject
import me.nagaev.veles.trust.InstallationTrustRepository
import me.nagaev.veles.trust.TrustEncoding
import me.nagaev.veles.trust.TrustException

/**
 * One admitted link. An address alone is not an identity across reconnects, so [serial]
 * increases monotonically on every accepted `STATE_CONNECTED` (design D4).
 */
data class ConnectionGeneration(val address: String, val serial: Long)

/** Lifecycle and record callbacks, scoped to a connection generation. */
interface ConnectionListener {
    fun onConnected(gen: ConnectionGeneration)

    /** The callee owns [bytes] and must zero it when it is done. */
    fun onRecord(gen: ConnectionGeneration, bytes: ByteArray)

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
 * Android transport adapter (design D4): one engine and one [Mutex] per generation. Every
 * engine call for a generation runs under that mutex, effects are applied in emission order,
 * and a result that arrives after the generation ended is dropped without touching the
 * transport. Inbound copies are zeroed after processing, or when the generation ends.
 *
 * The host never writes trust except [InstallationTrustRepository.markAuthenticated] on an
 * [SessionEffect.Authenticated] effect, still under the mutex, after re-checking that the
 * generation is current and the engine is open.
 */
@Suppress("TooManyFunctions") // Lifecycle callbacks, three public queries and the effect applier.
class SessionHost(
    private val scope: CoroutineScope,
    private val transport: SessionTransport,
    private val engineFactory: (ConnectionGeneration) -> PhoneSessionEngine,
    private val trust: InstallationTrustRepository,
    private val clock: () -> Long,
) : ConnectionListener {
    private class Live(val gen: ConnectionGeneration, val engine: PhoneSessionEngine) {
        val mutex = Mutex()
        @Volatile var ended = false
        @Volatile var tick: Job? = null

        /** Copies handed to [onRecord] and not yet zeroed; guarded by [registry]. */
        val inbound = mutableSetOf<ByteArray>()

        /** Set once the engine reports authentication; volatile so the probe can query it safely. */
        @Volatile var sessionClass: SessionClass? = null
        @Volatile var peerId: ByteArray? = null
    }

    private val registry = Any()
    private val sessions = LinkedHashMap<ConnectionGeneration, Live>()

    override fun onConnected(gen: ConnectionGeneration) {
        val live = Live(gen, engineFactory(gen))
        synchronized(registry) { sessions[gen] = live }
        live.tick = scope.launch { runTicks(live) }
    }

    override fun onRecord(gen: ConnectionGeneration, bytes: ByteArray) {
        val live = synchronized(registry) {
            sessions[gen]?.also { it.inbound += bytes }
        }
        if (live == null) {
            bytes.fill(0)
            return
        }
        scope.launch {
            try {
                live.mutex.withLock {
                    if (!isCurrent(live)) return@withLock
                    step(live) { live.engine.onInbound(bytes) }
                }
            } finally {
                synchronized(registry) { live.inbound -= bytes }
                bytes.fill(0)
            }
        }
    }

    override fun onSendComplete(gen: ConnectionGeneration, token: Long, ok: Boolean) {
        val live = synchronized(registry) { sessions[gen] } ?: return
        scope.launch {
            live.mutex.withLock {
                if (!isCurrent(live)) return@withLock
                step(live) { live.engine.onSendComplete(token, ok) }
            }
        }
    }

    override fun onDisconnected(gen: ConnectionGeneration) {
        val live = synchronized(registry) { sessions.remove(gen) } ?: return
        live.ended = true
        live.tick?.cancel()
        // Erase immediately when no engine call is in flight; otherwise queue the erasure and
        // teardown behind the suspended call so a late result can never be applied first.
        if (live.mutex.tryLock()) {
            try {
                teardown(live)
            } finally {
                live.mutex.unlock()
            }
        } else {
            scope.launch { live.mutex.withLock { teardown(live) } }
        }
    }

    /** Generations that fixed the FULL class and are still open. */
    fun openFullSessions(): List<ConnectionGeneration> = synchronized(registry) {
        sessions.values
            .filter { !it.ended && it.sessionClass == SessionClass.FULL }
            .map { it.gen }
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
        return live.mutex.withLock {
            if (!isCurrent(live) || !live.engine.isOpen) return@withLock false
            try {
                apply(live, live.engine.sendApplication(type, body))
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (_: SessionReject) {
                failedLocked(live)
                false
            } catch (_: Exception) {
                failedLocked(live)
                false
            }
        }
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
            live.mutex.withLock {
                if (!isCurrent(live)) return
                step(live) { live.engine.onTick() }
            }
            if (!isCurrent(live)) return
        }
    }

    private suspend fun step(live: Live, call: suspend () -> List<SessionEffect>) {
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
                is SessionEffect.Authenticated -> if (!markAuthenticated(live, effect)) return false
                is SessionEffect.Terminated -> Unit
                is SessionEffect.Deliver -> Unit // OTP-09 has no application consumer; OTP-10 adds one.
                SessionEffect.Close -> {
                    end(live)
                    transport.close(live.gen)
                }
            }
        }
        return sent
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

    private suspend fun markAuthenticated(live: Live, effect: SessionEffect.Authenticated): Boolean {
        try {
            if (!isCurrent(live) || !live.engine.isOpen) return false
            live.sessionClass = effect.sessionClass
            live.peerId = effect.peerId.copyOf()
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
    private fun failedLocked(live: Live) {
        live.engine.teardown()
        end(live)
        transport.close(live.gen)
    }

    /** Erases inbound copies and the engine; the caller already holds [Live.mutex]. */
    private fun teardown(live: Live) {
        synchronized(registry) { live.inbound.toList().also { live.inbound.clear() } }.forEach { it.fill(0) }
        live.engine.teardown()
    }

    private fun end(live: Live) {
        live.ended = true
        live.tick?.cancel()
        live.sessionClass = null
        live.peerId?.fill(0)
        live.peerId = null
        synchronized(registry) {
            if (sessions[live.gen] === live) sessions.remove(live.gen)
        }
    }

    private fun isCurrent(live: Live): Boolean =
        !live.ended && synchronized(registry) { sessions[live.gen] === live }

    private companion object {
        const val TICK_MILLIS = 1_000L
    }
}
