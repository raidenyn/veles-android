@file:Suppress("TooGenericExceptionCaught") // A test-only main must never fail on a refused send.

package me.nagaev.veles.session

import kotlinx.coroutines.CancellableContinuation
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import me.nagaev.veles.debugprobe.ElapsedClock
import me.nagaev.veles.debugprobe.GateBFixture
import me.nagaev.veles.debugprobe.GateBPullRouter
import me.nagaev.veles.debugprobe.GateBPushScheduler
import me.nagaev.veles.debugprobe.ProbeSession
import me.nagaev.veles.debugprobe.ProbeSessions
import me.nagaev.veles.debugprobe.StepTimer
import me.nagaev.veles.debugprobe.SyntheticProbeHandler
import me.nagaev.veles.debugprobe.WakeResource
import me.nagaev.veles.session.loopback.LoopbackIdentity
import me.nagaev.veles.session.loopback.LoopbackOutput
import me.nagaev.veles.session.loopback.classCode
import me.nagaev.veles.session.loopback.readMessage
import me.nagaev.veles.sharing.ConnectionGeneration
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.security.SecureRandom
import java.util.Random
import kotlin.coroutines.resume
import kotlin.system.exitProcess

/**
 * OTP-12 test-only Gate B loopback main (design D8): the real [GateBFixture] and
 * [GateBPushScheduler] behind the real [me.nagaev.veles.session.PhoneSessionEngine].
 *
 * It lives in `src/testDebug`, so the debug fixture is on its classpath and release unit tests
 * never compile it. The frame protocol and output events are the shared loopback's (see
 * `PhoneLoopbackMain`), with the Gate B commands:
 *
 * - `seed <lifetimes>` — comma list of five lifetimes, e.g. `seed 60,120,300,600,600`
 * - `schedule <delay>` — schedule one synthetic push in whole seconds
 * - `advance <seconds>` — advance the fixture/scheduler manual clock (never the session clock)
 * - `revoke` — remove the seed-derived `ACTIVE` record so a reconnect is rejected
 *
 * `pull`/`ack` are ordinary session records; `push` is the client expecting a `push_event`
 * after `schedule` plus `advance`. The session engine's clock is deliberately frozen at zero,
 * separate from the fixture clock, so a clock advance can never trip a session timeout; the
 * engine's real-time deadlines are covered by its own unit tests. No test sleeps: the schedule's
 * [StepTimer] suspends on [ManualStepTimer] until `advance` moves the manual clock.
 */
fun main(args: Array<String>) {
    if (args.size != 1) {
        System.err.println("usage: GateBLoopbackMainKt <seed>")
        exitProcess(2)
    }
    val seed = args[0]
    val phone = LoopbackIdentity.derive(PHONE_LABEL, PHONE_INSTALLATION_LABEL, seed)
    val client = LoopbackIdentity.derive(CLIENT_LABEL, CLIENT_INSTALLATION_LABEL, seed)
    val input = DataInputStream(BufferedInputStream(System.`in`))
    val output = LoopbackOutput(DataOutputStream(BufferedOutputStream(System.out)))
    GateBLoopback(phone, client, input, output).run()
}

private const val KIND_BYTES = 0
private const val KIND_COMMAND = 1
private const val NONCE_BYTES = 16
private const val MILLIS_PER_SECOND = 1_000L
private const val DEFAULT_LIFETIME_SECONDS = 600
private const val HISTORY_SIZE = 5
private const val FIXED_WALL_MILLIS = 1_700_000_000_000L
private const val FIXED_RANDOM_SEED = 0x12_0B_00_05L
private const val PHONE_LABEL = "Veles loopback phone identity v1"
private const val PHONE_INSTALLATION_LABEL = "Veles loopback phone installation v1"
private const val CLIENT_LABEL = "Veles loopback client identity v1"
private const val CLIENT_INSTALLATION_LABEL = "Veles loopback client installation v1"

private class GateBLoopback(
    private val phone: LoopbackIdentity,
    client: LoopbackIdentity,
    private val input: DataInputStream,
    private val output: LoopbackOutput,
) {
    private val clock = ManualElapsedClock()
    private val timer = ManualStepTimer(clock)
    private val random = Random(FIXED_RANDOM_SEED)
    private val log: (String) -> Unit = { message -> System.err.println("gate-b: $message") }
    private val fixture = GateBFixture(
        localInstallationId = phone.installationId.copyOf(),
        elapsed = ElapsedClock { clock.elapsedMillis() },
        wallMillis = { FIXED_WALL_MILLIS },
        random = { size -> ByteArray(size).also(random::nextBytes) },
        log = log,
    )
    private val probe = SyntheticProbeHandler(
        localInstallationId = phone.installationId.copyOf(),
        clock = { FIXED_WALL_MILLIS },
        random = { size -> ByteArray(size).also(random::nextBytes) },
        log = log,
    )
    private val router = GateBPullRouter(probe = probe, fixture = fixture, log = log)
    private val engineLock = Mutex()
    private val openSessions = mutableListOf<ProbeSession>()
    private val sessions = LoopbackProbeSessions()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    /** The seed-derived in-memory `ACTIVE` record standing in for an enrolled peer. */
    private var record: PeerRecord? = PeerRecord(
        installationId = client.installationId.copyOf(),
        identityPublicKey = client.publicKey.copyOf(),
        state = LocalRecordState.ACTIVE,
    )
    private var engine: PhoneSessionEngine? = null
    private var currentPrefix: ByteArray? = null
    private var serial = 0L
    private val scheduler = GateBPushScheduler(
        fixture = fixture,
        sessions = { sessions },
        clock = ElapsedClock { clock.elapsedMillis() },
        timer = timer,
        wake = NoWakeResource,
        scope = scope,
        processId = ProcessHandle.current().pid().toInt(),
        log = log,
    )

    fun run() = runBlocking {
        warmUp()
        try {
            while (true) {
                val (kind, payload) = readMessage(input) ?: break
                when (kind) {
                    KIND_BYTES -> engineLock.withLock {
                        val active = currentEngine()
                        apply(active, active.onInbound(payload))
                    }
                    KIND_COMMAND -> onCommand(payload.decodeToString())
                    else -> log("unknown kind $kind")
                }
            }
        } finally {
            scheduler.detach()
            fixture.detach()
        }
    }

    /**
     * The child JVM's first JCA signature and first engine construction can take tens of
     * seconds on a cold, large classpath. Both happen here, before the protocol loop starts,
     * so the first client hello is not delayed by class loading.
     */
    private suspend fun warmUp() {
        phone.signer.signDer(ByteArray(1))
        newEngine().teardown()
    }

    private suspend fun onCommand(text: String) {
        val command = text.trim()
        when {
            command == "seed" -> seed(List(HISTORY_SIZE) { DEFAULT_LIFETIME_SECONDS })
            command.startsWith("seed ") -> seed(parseLifetimes(command.removePrefix("seed ").trim()))
            command.startsWith("schedule ") -> schedule(command.removePrefix("schedule ").trim())
            command.startsWith("advance ") -> advance(command.removePrefix("advance ").trim())
            command == "revoke" -> revoke()
            else -> log("unknown command")
        }
    }

    private fun seed(lifetimes: List<Int>?) {
        if (lifetimes == null) {
            log("refused seed reason=lifetimes")
            return
        }
        fixture.seed(lifetimes)
    }

    private fun schedule(raw: String) {
        val delay = raw.toLongOrNull()
        if (delay == null) {
            log("refused schedule reason=delay")
            return
        }
        scheduler.schedule(delay)
    }

    private fun advance(raw: String) {
        val seconds = raw.toLongOrNull()
        if (seconds == null || seconds < 0L) {
            log("refused advance reason=seconds")
            return
        }
        timer.advance(seconds * MILLIS_PER_SECOND)
    }

    private suspend fun revoke() = engineLock.withLock {
        record = null
        engine?.teardown()
        output.closed(currentPrefix)
        currentPrefix = null
        openSessions.clear()
        engine = null
        log("revoked")
    }

    private fun parseLifetimes(raw: String): List<Int>? {
        if (raw.isEmpty()) return null
        val parts = raw.split(',').map(String::trim)
        if (parts.any { it.toIntOrNull() == null }) return null
        return parts.map(String::toInt)
    }

    /**
     * The engine is created on the first inbound byte, not at process start: the child JVM's
     * startup must never consume the protocol handshake deadline.
     */
    private fun currentEngine(): PhoneSessionEngine = engine ?: newEngine().also { engine = it }

    private fun newEngine(): PhoneSessionEngine = PhoneSessionEngine(
        localInstallationId = phone.installationId,
        signer = phone.signer,
        lookup = PeerLookup { id -> record?.takeIf { it.installationId.contentEquals(id) } },
        // Deliberately separate from the fixture's manual clock, and frozen. Real-time
        // handshake/idle deadlines made a loaded runner lose the journey before the client
        // finished its crypto, and this test-only main is not a timeout harness: the engine's
        // real-time deadlines are covered by its own unit tests. `advance` therefore can never
        // expire an open session.
        clock = { 0L },
        nonce = { ByteArray(NONCE_BYTES).also { SecureRandom().nextBytes(it) } },
        handler = router,
    )

    private suspend fun apply(active: PhoneSessionEngine, effects: List<SessionEffect>) {
        for (effect in effects) {
            when (effect) {
                is SessionEffect.Send -> {
                    output.frame(effect.bytes)
                    effect.completionToken?.let { token ->
                        apply(active, active.onSendComplete(token, true))
                    }
                }
                is SessionEffect.Authenticated -> {
                    currentPrefix = effect.sessionIdPrefix.copyOf()
                    if (effect.sessionClass == SessionClass.FULL) {
                        serial += 1
                        openSessions += ProbeSession(
                            ConnectionGeneration("loopback", serial),
                            effect.sessionIdPrefix.copyOf(),
                        )
                    }
                    output.authenticated(classCode(effect.sessionClass), effect.sessionIdPrefix)
                    for (record in effect.unprompted) {
                        apply(active, active.sendApplication(record.type, record.body))
                    }
                }
                is SessionEffect.Deliver -> Unit
                is SessionEffect.Terminated -> log("terminated ${effect.reason}")
                SessionEffect.Close -> {
                    output.closed(currentPrefix)
                    currentPrefix = null
                    openSessions.clear()
                }
            }
        }
    }

    private inner class LoopbackProbeSessions : ProbeSessions {
        override fun openFullSessions(): List<ProbeSession> = openSessions.toList()

        override suspend fun sendApplication(
            gen: ConnectionGeneration,
            type: Int,
            body: ByteArray,
        ): Boolean = engineLock.withLock {
            if (openSessions.none { it.generation == gen }) return@withLock false
            try {
                val active = currentEngine()
                apply(active, active.sendApplication(type, body))
                true
            } catch (_: Exception) {
                false
            }
        }
    }
}

/** A monotonic manual clock: only the `advance` command moves it. */
private class ManualElapsedClock {
    private var value = 0L

    @Synchronized
    fun elapsedMillis(): Long = value

    @Synchronized
    fun advance(millis: Long) {
        value += millis
    }
}

/**
 * The scheduler's manual timer. `pause` suspends until `advance` moves the manual clock;
 * `advance` resumes every parked step so each one re-reads the clock. Cancellation (the
 * scheduler's detach) removes the waiter, so no continuation is resumed twice.
 */
private class ManualStepTimer(private val clock: ManualElapsedClock) : StepTimer {
    private class Waiter(val continuation: CancellableContinuation<Unit>)

    /** Guards the waiters, the clock advance and the lost-wake-up counter together. */
    private val lock = Any()
    private val waiters = mutableListOf<Waiter>()

    /**
     * Set by every advance. A pause that starts after the advance but before the scheduler
     * re-reads the clock consumes it and returns at once, so a remaining-time computation that
     * raced the advance cannot park until a second advance that never comes.
     */
    private var pendingAdvance = false

    override suspend fun pause(millis: Long) {
        if (millis <= 0L) return
        suspendCancellableCoroutine { continuation ->
            val immediate = synchronized(lock) {
                if (pendingAdvance) {
                    pendingAdvance = false
                    true
                } else {
                    waiters += Waiter(continuation)
                    false
                }
            }
            if (immediate) {
                continuation.resume(Unit)
            } else {
                continuation.invokeOnCancellation {
                    synchronized(lock) { waiters.removeAll { it.continuation === continuation } }
                }
            }
        }
    }

    /** Advances the manual clock and resumes every parked step so each one re-reads it. */
    fun advance(millis: Long) {
        val ready: List<Waiter>
        synchronized(lock) {
            clock.advance(millis)
            pendingAdvance = true
            ready = waiters.toList()
            waiters.clear()
        }
        for (waiter in ready) {
            if (waiter.continuation.isActive) waiter.continuation.resume(Unit)
        }
    }
}

/** The test-only main runs no real wake lock; the scheduler still owns its acquire/release. */
private object NoWakeResource : WakeResource {
    override fun acquire(timeoutMillis: Long) = Unit

    override fun release() = Unit
}
