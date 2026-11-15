package me.nagaev.veles.session

import android.content.Context
import dagger.hilt.android.EntryPointAccessors
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.runBlocking
import me.nagaev.veles.debugpairing.PairingDebugHooks
import me.nagaev.veles.debugprobe.DebugProbeEntryPoint
import me.nagaev.veles.debugprobe.DebugProbeRuntime
import me.nagaev.veles.debugprobe.DebugRevocationRuntime
import me.nagaev.veles.debugprobe.PROBE_TAG
import me.nagaev.veles.debugprobe.ProbeReceiver
import me.nagaev.veles.debugprobe.SyntheticProbeHandler
import me.nagaev.veles.sharing.PairingEndpoint
import me.nagaev.veles.sharing.SessionHost
import me.nagaev.veles.trust.ActivationVerifier
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
 * Debug build type of the session extension point: the synthetic probe (design D5).
 * The release build type keeps the null handler and the no-op attach.
 */
object BuildSessionExtensions {
    /**
     * The synthetic probe needs its own installation id. A store that cannot supply one (corrupt
     * payload, missing identity) must not abort the service wiring: the probe handler is simply
     * absent, while the production activation and removal handlers still run and the explicit
     * reset stays reachable (OTP-11 design D6).
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
        val handler = SyntheticProbeHandler(
            localInstallationId = TrustEncoding.bytes(identity.installationId),
            clock = System::currentTimeMillis,
            random = { size -> ProbeReceiver.randomBytes(size) },
            log = { line -> logger.d(PROBE_TAG, line) },
        )
        DebugProbeRuntime.eventSource = handler
        return handler
    }

    fun attach(host: SessionHost) {
        DebugProbeRuntime.host = host
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
