package me.nagaev.veles.session

import android.content.Context
import me.nagaev.veles.sharing.PairingEndpoint
import me.nagaev.veles.sharing.SessionHost
import me.nagaev.veles.trust.PeerRevocationCoordinator

/**
 * Release build type of the session extension point. OTP-09 ships the stub so that no
 * application record has a handler: every authorized application record fails closed with
 * `app_error` `protocol_violation` until OTP-10 adds the production handler.
 */
object BuildSessionExtensions {
    @Suppress("UnusedParameter")
    fun applicationHandler(context: Context): ApplicationHandler? = null

    @Suppress("UnusedParameter")
    fun attach(host: SessionHost) = Unit

    /** Release builds expose no debug peer-management control (OTP-11 D9). */
    @Suppress("UnusedParameter")
    internal fun attachRevocation(coordinator: PeerRevocationCoordinator) = Unit

    internal fun detachRevocation() = Unit

    /** Release builds can never open (or cancel) a pairing window (design D5). */
    @Suppress("UnusedParameter")
    internal fun attachPairing(pairing: PairingEndpoint, onCancel: () -> Unit) = Unit

    /** Release builds have no fingerprint surface. */
    @Suppress("UnusedParameter")
    internal fun publishPairFingerprint(value: String) = Unit

    internal fun detachPairing() = Unit
}
