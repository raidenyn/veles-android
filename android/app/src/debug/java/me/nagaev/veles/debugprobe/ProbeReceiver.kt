@file:Suppress("TooGenericExceptionCaught") // A debug receiver must never crash the app.

package me.nagaev.veles.debugprobe

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.SystemClock
import dagger.hilt.EntryPoint
import dagger.hilt.InstallIn
import dagger.hilt.android.EntryPointAccessors
import dagger.hilt.components.SingletonComponent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import me.nagaev.veles.common.VelesLog
import me.nagaev.veles.session.RecordType
import me.nagaev.veles.session.SessionClass
import me.nagaev.veles.session.debugRejectAllVerifier
import me.nagaev.veles.sharing.ConnectionGeneration
import me.nagaev.veles.sharing.SessionHost
import me.nagaev.veles.trust.InstallationTrustRepository
import me.nagaev.veles.trust.RoomTrustStorage
import me.nagaev.veles.trust.TrustDatabase
import me.nagaev.veles.trust.TrustException
import me.nagaev.veles.trust.createAndroidTrustRepository
import java.security.SecureRandom

/** One authenticated destination: its generation and its own 8-byte session id prefix. */
internal class ProbeSession(
    val generation: ConnectionGeneration,
    val sessionIdPrefix: ByteArray,
)

/** Session surface the debug commands need, kept free of the transport adapter. */
internal interface ProbeSessions {
    fun openFullSessions(): List<ProbeSession>

    suspend fun sendApplication(gen: ConnectionGeneration, type: Int, body: ByteArray): Boolean
}

internal class SessionProbeSessions(private val host: SessionHost) : ProbeSessions {
    override fun openFullSessions(): List<ProbeSession> = host.authenticatedSessions()
        .filter { it.sessionClass == SessionClass.FULL }
        .map { ProbeSession(it.generation, it.sessionIdPrefix) }

    override suspend fun sendApplication(gen: ConnectionGeneration, type: Int, body: ByteArray): Boolean = host.sendApplication(gen, type, body)
}

/** No running host: the empty session surface, also reused when the runtime host is null. */
internal object NoSessions : ProbeSessions {
    override fun openFullSessions(): List<ProbeSession> = emptyList()

    override suspend fun sendApplication(gen: ConnectionGeneration, type: Int, body: ByteArray): Boolean = false
}

/** Process-local debug wiring: the running host and the handler that owns the probe marker. */
internal object DebugProbeRuntime {
    @Volatile
    var host: SessionHost? = null

    @Volatile
    var eventSource: ProbeEventSource? = null

    /** The Gate B fixture, built beside the probe when the installation identity is available (OTP-12). */
    @Volatile
    var fixture: GateBFixture? = null

    /**
     * The Gate B command surface (OTP-12): the fixture plus its push scheduler, created by the
     * debug session extension's `attach` and cleared by `detach` so no schedule outlives a host.
     */
    @Volatile
    var gateB: GateBController? = null

    /**
     * The application context the fixture's adb commands run from. Set by the debug session
     * extension so a later schedule command cannot hold an activity or receiver context.
     */
    @Volatile
    var applicationContext: Context? = null

    /**
     * The one elapsed-realtime clock the Gate B fixture is built with; the adb commands log
     * outcome timestamps from this same instance instead of introducing a second clock.
     */
    @Volatile
    var elapsed: ElapsedClock = ElapsedClock { SystemClock.elapsedRealtime() }
}

/**
 * The adb command surface (design D5, OTP-12 design D3/D4). Every command is a shell-only
 * broadcast (`android.permission.DUMP`) to the explicit `debugprobe.ProbeReceiver` component.
 *
 * The Gate B commands parse only their extras and forward to [gateB]: the fixture and the
 * scheduler keep their own validation rules and messages, so an out-of-range delay or a bad
 * lifetime list is refused by the component that owns the rule. Every command reports one bounded
 * structured result and logs the same outcome with an elapsed-realtime timestamp.
 */
internal class ProbeCommands(
    private val sessions: ProbeSessions,
    private val repository: InstallationTrustRepository,
    private val injector: DebugTrustInjector,
    private val handler: ProbeEventSource,
    private val log: (String) -> Unit,
    /** Shell-only structured result channel (`am broadcast` prints the ordered result data). */
    private val result: (String) -> Unit = {},
    /**
     * OTP-12: while the Gate B fixture is armed it owns synthetic pushes, so the adb push is
     * refused and logged instead of sending a probe event. Task 3 keeps this beside its `gateB`.
     */
    private val gateBArmed: () -> Boolean = { false },
    /**
     * OTP-12: the Gate B fixture commands (seed, disarm, schedule, cancel, detach). `null` when
     * no sharing service is running; the Gate B actions report that instead of scheduling.
     */
    internal val gateB: GateBController? = null,
    /**
     * The same injected elapsed-realtime clock the Gate B fixture uses (design D6): refusal
     * outcomes carry an elapsed timestamp, never a wall-clock read.
     */
    private val elapsed: ElapsedClock = ElapsedClock { SystemClock.elapsedRealtime() },
    /**
     * OTP-11: the coordinator the debug Clear goes through, so clearing a stranded or injected
     * record also terminates that peer's open sessions instead of being refused. `null` when no
     * sharing service is running (then no session can exist and the repository delete is enough).
     */
    private val revocation: PeerManagement? = null,
) {
    suspend fun exportIdentity() {
        try {
            val identity = repository.identity()
            // The full values travel only through the ordered-broadcast result that the adb
            // shell prints; the log keeps bounded prefixes so plain logcat never carries an
            // installation id or identity key in full.
            result("identity=${identity.installationId} pub=${identity.publicKey}")
            log("identity=${identity.installationId.take(LOG_PREFIX_CHARS)} pub=${identity.publicKey.take(LOG_PREFIX_CHARS)}")
        } catch (_: TrustException) {
            log("identity unavailable")
        }
    }

    suspend fun injectPeer(id: String, pub: String) {
        try {
            // Creates the durable identity when the sharing service has not started yet.
            repository.identity()
            injector.injectActive(id, pub)
            log("injected peer=${id.take(LOG_PREFIX_CHARS)}")
        } catch (_: TrustException) {
            log("inject refused peer=${id.take(LOG_PREFIX_CHARS)}")
        }
    }

    suspend fun push() {
        // Gate B owns synthetic pushes while its fixture is armed; a probe push here would
        // bypass the fixture's schedule and history (refusal outcome, design D6).
        if (gateBArmed()) {
            log("elapsed=${elapsed.elapsedMillis()} fixture owns pushes")
            return
        }
        val open = sessions.openFullSessions()
        if (open.isEmpty()) return
        // One fresh event per destination, logged with that destination's own session prefix.
        open.forEach { session ->
            val event = handler.next(session.sessionIdPrefix)
            if (!sessions.sendApplication(session.generation, RecordType.PUSH_EVENT.wire, event.body)) {
                log("push refused gen=${session.generation.serial}")
            }
        }
    }

    /**
     * OTP-11 "Clear incomplete record": the clear goes through the revocation coordinator, which
     * deletes the record and terminates any open session of that peer instead of refusing. There
     * is no direct trust-store fallback: without a running coordinator the clear is unavailable
     * and nothing is touched, because a nullable runtime snapshot is not proof that no service or
     * no session can start before the delete.
     */
    suspend fun revokePeer(id: String): Boolean {
        val coordinator = revocation
        if (coordinator == null) {
            log("revoke unavailable peer=${id.take(LOG_PREFIX_CHARS)}")
            return false
        }
        return try {
            coordinator.revoke(id)
            log("revoked peer=${id.take(LOG_PREFIX_CHARS)}")
            true
        } catch (_: TrustException) {
            log("revoke refused peer=${id.take(LOG_PREFIX_CHARS)}")
            false
        }
    }

    /**
     * OTP-12 `GATE_B_SEED`: a missing `lifetimes` extra means the five-600 default; otherwise a
     * comma list of values that must all be whole integers. The fixture owns the five-value and
     * 1..600 checks and logs its own refusal reason. Without a running controller (no sharing
     * service) nothing is armed and the refusal is logged and reported.
     */
    fun gateBSeed(lifetimes: String?): Boolean {
        val controller = gateB
        if (controller == null) {
            refuse("seed", REASON_HOST)
            return false
        }
        val parsed = parseLifetimes(lifetimes)
        if (parsed == null) {
            refuse("seed", "lifetimes")
            return false
        }
        if (!controller.seed(parsed)) {
            outcome("gate-b seed refused")
            return false
        }
        outcome("gate-b seed ok lifetimes=${parsed.joinToString(",")}")
        return true
    }

    fun gateBDisarm() {
        val controller = gateB
        if (controller == null) {
            refuse("disarm", REASON_HOST)
            return
        }
        controller.disarm()
        outcome("gate-b disarm ok")
    }

    /**
     * OTP-12 `GATE_B_SCHEDULE_PUSH`: the `delay` extra must be present and a whole-second integer.
     * The scheduler owns the 1..7200 range and the armed requirement, so those values are
     * forwarded unchanged and refused with its own message.
     */
    fun gateBSchedulePush(delay: String?): Boolean {
        val controller = gateB
        if (controller == null) {
            refuse("schedule", REASON_HOST)
            return false
        }
        val seconds = delay?.trim()?.toLongOrNull()
        if (seconds == null) {
            refuse("schedule", "delay")
            return false
        }
        if (!controller.schedule(seconds)) {
            outcome("gate-b schedule refused delay=$seconds")
            return false
        }
        outcome("gate-b schedule ok delay=$seconds")
        return true
    }

    fun gateBCancelPush() {
        val controller = gateB
        if (controller == null) {
            refuse("cancel", REASON_HOST)
            return
        }
        controller.cancel()
        outcome("gate-b cancel ok")
    }

    /** One structured result plus the same bounded outcome line with the elapsed timestamp. */
    private fun outcome(line: String) {
        result(line)
        log("elapsed=${elapsed.elapsedMillis()} $line")
    }

    private fun refuse(command: String, reason: String) = outcome("gate-b $command refused reason=$reason")

    /** A missing extra is the five-600 default; a malformed value is refused, never defaulted. */
    private fun parseLifetimes(raw: String?): List<Int>? {
        if (raw == null) return List(GATE_B_HISTORY_SIZE) { GATE_B_DEFAULT_LIFETIME_SECONDS }
        val parts = raw.split(',').map(String::trim)
        if (parts.any { it.toIntOrNull() == null }) return null
        return parts.map { it.toInt() }
    }

    private companion object {
        /** Bounded log prefix: enough to correlate two log lines, never a full identifier. */
        private const val LOG_PREFIX_CHARS = 8

        /** The fixture's own history size and default lifetime, forwarded on a bare seed. */
        private const val GATE_B_HISTORY_SIZE = 5
        private const val GATE_B_DEFAULT_LIFETIME_SECONDS = 600

        /** No sharing service: the controller is published only while a host is attached. */
        private const val REASON_HOST = "host"
    }
}

@EntryPoint
@InstallIn(SingletonComponent::class)
internal interface DebugProbeEntryPoint {
    fun velesLog(): VelesLog
}

/**
 * The one debug command wiring used by the adb receiver and the pairing screen's Clear
 * control, so both paths share the existing `REVOKE_PEER` refusal and log behavior.
 */
internal fun debugProbeCommands(
    context: Context,
    log: (String) -> Unit,
    result: (String) -> Unit = {},
): ProbeCommands = ProbeCommands(
    sessions = DebugProbeRuntime.host?.let(::SessionProbeSessions) ?: NoSessions,
    repository = createAndroidTrustRepository(context, debugRejectAllVerifier),
    injector = DebugTrustInjector(
        storage = RoomTrustStorage(TrustDatabase.open(context)),
        elapsedMillis = System::currentTimeMillis,
        wallMillis = System::currentTimeMillis,
    ),
    handler = DebugProbeRuntime.eventSource ?: ProbeReceiver.EMPTY_EVENT_SOURCE,
    log = log,
    result = result,
    gateBArmed = { DebugProbeRuntime.fixture?.armed == true },
    gateB = DebugProbeRuntime.gateB,
    elapsed = DebugProbeRuntime.elapsed,
    revocation = DebugRevocationRuntime.management(),
)

class ProbeReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context?, intent: Intent?) {
        val action = intent?.action ?: return
        if (context == null) return
        val id = intent.getStringExtra(EXTRA_ID)
        val pub = intent.getStringExtra(EXTRA_PUB)
        val lifetimes = intent.getStringExtra(EXTRA_LIFETIMES)
        val delay = intent.getStringExtra(EXTRA_DELAY)
        val application = context.applicationContext
        val pending = goAsync()
        CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
            try {
                // `goAsync()` detaches the pending result from this receiver; the result
                // callback must go through the PendingResult (the receiver's own
                // `setResultData` throws "Call while result is not pending").
                dispatch(application, action, id, pub, lifetimes, delay, pending::setResultData)
            } finally {
                pending.finish()
            }
        }
    }

    private suspend fun dispatch(
        context: Context,
        action: String,
        id: String?,
        pub: String?,
        lifetimes: String?,
        delay: String?,
        result: (String) -> Unit,
    ) {
        val logger = EntryPointAccessors.fromApplication(context, DebugProbeEntryPoint::class.java).velesLog()
        val log: (String) -> Unit = { logger.d(PROBE_TAG, it) }
        val commands = debugProbeCommands(context, log, result)
        when (action) {
            ACTION_EXPORT_IDENTITY -> commands.exportIdentity()
            ACTION_INJECT_PEER -> if (id != null && pub != null) commands.injectPeer(id, pub) else log("inject refused")
            ACTION_PUSH -> commands.push()
            ACTION_REVOKE_PEER -> if (id != null) commands.revokePeer(id) else log("revoke refused")
            ACTION_GATE_B_SEED -> commands.gateBSeed(lifetimes)
            ACTION_GATE_B_DISARM -> commands.gateBDisarm()
            ACTION_GATE_B_SCHEDULE_PUSH -> commands.gateBSchedulePush(delay)
            ACTION_GATE_B_CANCEL_PUSH -> commands.gateBCancelPush()
        }
    }

    companion object {
        const val ACTION_EXPORT_IDENTITY = "me.nagaev.veles.debug.EXPORT_IDENTITY"
        const val ACTION_INJECT_PEER = "me.nagaev.veles.debug.INJECT_PEER"
        const val ACTION_PUSH = "me.nagaev.veles.debug.PUSH"
        const val ACTION_REVOKE_PEER = "me.nagaev.veles.debug.REVOKE_PEER"

        /** OTP-12 Gate B fixture commands: seed, disarm, schedule one push and cancel it. */
        const val ACTION_GATE_B_SEED = "me.nagaev.veles.debug.GATE_B_SEED"
        const val ACTION_GATE_B_DISARM = "me.nagaev.veles.debug.GATE_B_DISARM"
        const val ACTION_GATE_B_SCHEDULE_PUSH = "me.nagaev.veles.debug.GATE_B_SCHEDULE_PUSH"
        const val ACTION_GATE_B_CANCEL_PUSH = "me.nagaev.veles.debug.GATE_B_CANCEL_PUSH"

        const val EXTRA_ID = "id"
        const val EXTRA_PUB = "pub"

        /** Comma list of five lifetimes in 1..600; absent means five values of 600. */
        const val EXTRA_LIFETIMES = "lifetimes"

        /** Whole-second delay in 1..7200 for the one scheduled push. */
        const val EXTRA_DELAY = "delay"

        internal val EMPTY_EVENT_SOURCE = ProbeEventSource { _ ->
            SyntheticEvent(code = "", eventId = ByteArray(0), body = ByteArray(0))
        }

        /** Fresh OS randomness; the marker is derived from it and never stored. */
        val secureRandom = SecureRandom()

        fun randomBytes(size: Int): ByteArray = ByteArray(size).also(secureRandom::nextBytes)
    }
}
