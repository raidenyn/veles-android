package me.nagaev.veles.session

import android.content.Context
import android.os.Process
import android.os.SystemClock
import dagger.hilt.android.EntryPointAccessors
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.runBlocking
import me.nagaev.veles.debugpairing.PairingDebugHooks
import me.nagaev.veles.debugprobe.CoroutineStepTimer
import me.nagaev.veles.debugprobe.DebugProbeEntryPoint
import me.nagaev.veles.debugprobe.DebugProbeRuntime
import me.nagaev.veles.debugprobe.DebugRevocationRuntime
import me.nagaev.veles.debugprobe.ElapsedClock
import me.nagaev.veles.debugprobe.GateBController
import me.nagaev.veles.debugprobe.GateBFixture
import me.nagaev.veles.debugprobe.GateBPullRouter
import me.nagaev.veles.debugprobe.GateBPushScheduler
import me.nagaev.veles.debugprobe.NoSessions
import me.nagaev.veles.debugprobe.PROBE_TAG
import me.nagaev.veles.debugprobe.PowerManagerWakeResource
import me.nagaev.veles.debugprobe.ProbeReceiver
import me.nagaev.veles.debugprobe.SessionProbeSessions
import me.nagaev.veles.debugprobe.SyntheticProbeHandler
import me.nagaev.veles.sharing.PairingEndpoint
import me.nagaev.veles.sharing.SessionHost
import me.nagaev.veles.trust.ActivationVerifier
import me.nagaev.veles.trust.InstallationIdentity
import me.nagaev.veles.trust.PeerRevocationCoordinator
import me.nagaev.veles.trust.TrustEncoding
import me.nagaev.veles.trust.TrustException
import me.nagaev.veles.trust.createAndroidTrustRepository

/**
 * The debug build type's activation verifier. Debug repositories are never the production
 * session repository (OTP-10 plan 9d): they stay reject-all, and this exact value is what both
 * debug call sites install and the unit test exercises.
 */
internal val debugRejectAllVerifier: ActivationVerifier = ActivationVerifier { _, _, _ -> false }

/**
 * The pure composition branch behind [BuildSessionExtensions.applicationHandler] (OTP-12 design
 * D5): with an identity it builds the synthetic probe and the Gate B fixture from the same
 * installation id, publishes both to [DebugProbeRuntime] and returns the pull router that picks
 * exactly one responder per pull. Without an identity it returns null and builds nothing, as the
 * probe is absent today.
 *
 * Every side effect is injected so the branch can be exercised without Android, Hilt or
 * Robolectric; only the defaults touch the platform clocks and OS randomness.
 */
internal fun buildDebugHandler(
    identity: InstallationIdentity?,
    log: (String) -> Unit,
    elapsed: ElapsedClock = ElapsedClock { SystemClock.elapsedRealtime() },
    clock: () -> Long = System::currentTimeMillis,
    wallMillis: () -> Long = System::currentTimeMillis,
    random: (Int) -> ByteArray = { size -> ProbeReceiver.randomBytes(size) },
): ApplicationHandler? {
    if (identity == null) return null
    val installationId = TrustEncoding.bytes(identity.installationId)
    val probe = SyntheticProbeHandler(
        localInstallationId = installationId,
        clock = clock,
        random = random,
        log = log,
    )
    val fixture = GateBFixture(
        localInstallationId = installationId,
        elapsed = elapsed,
        wallMillis = wallMillis,
        random = random,
        log = log,
    )
    DebugProbeRuntime.eventSource = probe
    DebugProbeRuntime.fixture = fixture
    // The adb commands log refusal timestamps from this same instance, never a second clock.
    DebugProbeRuntime.elapsed = elapsed
    return GateBPullRouter(probe = probe, fixture = fixture, log = log)
}

/**
 * Debug build type of the session extension point: the synthetic probe (design D5).
 * The release build type keeps the null handler and the no-op attach.
 */
object BuildSessionExtensions {
    /**
     * The synthetic probe needs its own installation id. A store that cannot supply one (corrupt
     * payload, missing identity) must not abort the service wiring: the probe handler is simply
     * absent, while the production activation and removal handlers still run and the explicit
     * reset stays reachable (OTP-11 design D6).
     *
     * OTP-12: the Gate B fixture is built beside the probe, and the returned handler is the pull
     * router that picks one responder per pull. The application context is kept here so a later
     * fixture command never holds a shorter-lived context.
     */
    fun applicationHandler(context: Context): ApplicationHandler? {
        val logger = EntryPointAccessors.fromApplication(
            context.applicationContext,
            DebugProbeEntryPoint::class.java,
        ).velesLog()
        val repository = createAndroidTrustRepository(context, debugRejectAllVerifier)
        val identity = try {
            runBlocking { repository.identity() }
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (_: TrustException) {
            return null
        }
        val handler = buildDebugHandler(identity, log = { line -> logger.d(PROBE_TAG, line) })
        if (handler != null) DebugProbeRuntime.applicationContext = context.applicationContext
        return handler
    }

    fun attach(host: SessionHost) {
        DebugProbeRuntime.host = host
        val fixture = DebugProbeRuntime.fixture ?: return
        val context = DebugProbeRuntime.applicationContext ?: return
        val logger = EntryPointAccessors.fromApplication(
            context,
            DebugProbeEntryPoint::class.java,
        ).velesLog()
        // Never Main: detach() joins this job with runBlocking from onDestroy on the main thread.
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        val scheduler = GateBPushScheduler(
            fixture = fixture,
            sessions = { DebugProbeRuntime.host?.let(::SessionProbeSessions) ?: NoSessions },
            clock = DebugProbeRuntime.elapsed,
            timer = CoroutineStepTimer,
            wake = PowerManagerWakeResource(context),
            scope = scope,
            processId = Process.myPid(),
            log = { line -> logger.d(PROBE_TAG, line) },
        )
        DebugProbeRuntime.gateB = GateBController(fixture = fixture, scheduler = scheduler)
    }

    /**
     * OTP-12 D5: the sharing service calls this before the host shuts down, so a scheduled push
     * can never fire against a closing host. `fail()` and `onDestroy()` both reach the call, so
     * a second detach is harmless: disarm and cancel are idempotent and the scheduler join runs
     * on an already-completed job. The runtime references are cleared last, after the join.
     */
    fun detach() {
        DebugProbeRuntime.gateB?.detach()
        DebugProbeRuntime.gateB = null
        DebugProbeRuntime.host = null
    }

    /**
     * OTP-11 D9: hand the running coordinator to the debug peer-management controls. They drive
     * the coordinator's own operations, so a debug Revoke, Mutual remove, Reset or Clear behaves
     * exactly like the production path.
     */
    internal fun attachRevocation(coordinator: PeerRevocationCoordinator) {
        DebugRevocationRuntime.attach(coordinator)
    }

    internal fun detachRevocation() {
        DebugRevocationRuntime.detach()
    }

    /** OTP-10 D5: expose the production window and its cancel to the debug surface. */
    internal fun attachPairing(pairing: PairingEndpoint, onCancel: () -> Unit) {
        PairingDebugHooks.attach(pairing.window, onCancel)
    }

    /** OTP2-REC-011: publish the public pair fingerprint to the debug screen. */
    internal fun publishPairFingerprint(value: String) {
        PairingDebugHooks.publishFingerprint(value)
    }

    internal fun detachPairing() {
        PairingDebugHooks.detach()
    }
}
