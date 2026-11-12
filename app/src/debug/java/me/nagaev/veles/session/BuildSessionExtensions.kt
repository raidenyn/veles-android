package me.nagaev.veles.session

import android.content.Context
import dagger.hilt.android.EntryPointAccessors
import kotlinx.coroutines.runBlocking
import me.nagaev.veles.debugprobe.DebugProbeEntryPoint
import me.nagaev.veles.debugprobe.DebugProbeRuntime
import me.nagaev.veles.debugprobe.PROBE_TAG
import me.nagaev.veles.debugprobe.ProbeReceiver
import me.nagaev.veles.debugprobe.SyntheticProbeHandler
import me.nagaev.veles.sharing.SessionHost
import me.nagaev.veles.trust.ActivationVerifier
import me.nagaev.veles.trust.TrustEncoding
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
    fun applicationHandler(context: Context): ApplicationHandler {
        val logger = EntryPointAccessors.fromApplication(
            context.applicationContext,
            DebugProbeEntryPoint::class.java,
        ).velesLog()
        val repository = createAndroidTrustRepository(context, debugRejectAllVerifier)
        val identity = runBlocking { repository.identity() }
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
}
