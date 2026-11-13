@file:Suppress("TooGenericExceptionCaught") // A debug receiver must never crash the app.

package me.nagaev.veles.debugprobe

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
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

    fun hasOpenSession(peerId: String): Boolean

    suspend fun sendApplication(gen: ConnectionGeneration, type: Int, body: ByteArray): Boolean
}

internal class SessionProbeSessions(private val host: SessionHost) : ProbeSessions {
    override fun openFullSessions(): List<ProbeSession> = host.authenticatedSessions()
        .filter { it.sessionClass == SessionClass.FULL }
        .map { ProbeSession(it.generation, it.sessionIdPrefix) }

    override fun hasOpenSession(peerId: String): Boolean = host.hasOpenSession(peerId)

    override suspend fun sendApplication(gen: ConnectionGeneration, type: Int, body: ByteArray): Boolean = host.sendApplication(gen, type, body)
}

private object NoSessions : ProbeSessions {
    override fun openFullSessions(): List<ProbeSession> = emptyList()

    override fun hasOpenSession(peerId: String): Boolean = false

    override suspend fun sendApplication(gen: ConnectionGeneration, type: Int, body: ByteArray): Boolean = false
}

/** Process-local debug wiring: the running host and the handler that owns the probe marker. */
internal object DebugProbeRuntime {
    @Volatile
    var host: SessionHost? = null

    @Volatile
    var eventSource: ProbeEventSource? = null
}

/**
 * The adb command surface (design D5). Every command is a shell-only broadcast
 * (`android.permission.DUMP`) to the explicit `debugprobe.ProbeReceiver` component.
 */
internal class ProbeCommands(
    private val sessions: ProbeSessions,
    private val repository: InstallationTrustRepository,
    private val injector: DebugTrustInjector,
    private val handler: ProbeEventSource,
    private val log: (String) -> Unit,
    /** Shell-only structured result channel (`am broadcast` prints the ordered result data). */
    private val result: (String) -> Unit = {},
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

    suspend fun revokePeer(id: String): Boolean {
        if (sessions.hasOpenSession(id)) {
            // Never the OTP-11 live revocation: refusal is the debug contract.
            log("revoke refused peer=${id.take(LOG_PREFIX_CHARS)} session_open=true")
            return false
        }
        return try {
            repository.revoke(id)
            log("revoked peer=${id.take(LOG_PREFIX_CHARS)}")
            true
        } catch (_: TrustException) {
            log("revoke refused peer=${id.take(LOG_PREFIX_CHARS)}")
            false
        }
    }

    private companion object {
        /** Bounded log prefix: enough to correlate two log lines, never a full identifier. */
        private const val LOG_PREFIX_CHARS = 8
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
)

class ProbeReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context?, intent: Intent?) {
        val action = intent?.action ?: return
        if (context == null) return
        val id = intent.getStringExtra(EXTRA_ID)
        val pub = intent.getStringExtra(EXTRA_PUB)
        val application = context.applicationContext
        val pending = goAsync()
        CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
            try {
                // `goAsync()` detaches the pending result from this receiver; the result
                // callback must go through the PendingResult (the receiver's own
                // `setResultData` throws "Call while result is not pending").
                dispatch(application, action, id, pub, pending::setResultData)
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
        }
    }

    companion object {
        const val ACTION_EXPORT_IDENTITY = "me.nagaev.veles.debug.EXPORT_IDENTITY"
        const val ACTION_INJECT_PEER = "me.nagaev.veles.debug.INJECT_PEER"
        const val ACTION_PUSH = "me.nagaev.veles.debug.PUSH"
        const val ACTION_REVOKE_PEER = "me.nagaev.veles.debug.REVOKE_PEER"
        const val EXTRA_ID = "id"
        const val EXTRA_PUB = "pub"

        internal val EMPTY_EVENT_SOURCE = ProbeEventSource { _ ->
            SyntheticEvent(code = "", eventId = ByteArray(0), body = ByteArray(0))
        }

        /** Fresh OS randomness; the marker is derived from it and never stored. */
        val secureRandom = SecureRandom()

        fun randomBytes(size: Int): ByteArray = ByteArray(size).also(secureRandom::nextBytes)
    }
}
