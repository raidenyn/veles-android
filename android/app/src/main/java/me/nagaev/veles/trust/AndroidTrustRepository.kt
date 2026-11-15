package me.nagaev.veles.trust

import android.content.Context
import android.os.SystemClock
import android.provider.Settings

/** Caller supplies the authenticated session's activation verifier (OTP-09/10).
 * There is deliberately no accept-all verifier or UI activation shortcut.
 *
 * [onStoreFailure] is the composition root's fail-closed action (OTP-11 design D6): the
 * repository invokes it once per store-failure episode, whichever caller observed the failure.
 */
fun createAndroidTrustRepository(
    context: Context,
    verifier: ActivationVerifier,
    onStoreFailure: () -> Unit = {},
): TrustRepository = TrustRepository(
    RoomTrustStorage(TrustDatabase.open(context)),
    AndroidIdentityKeyStore(),
    SystemClock::elapsedRealtime,
    {
        // BOOT_COUNT detects reboot; the repository atomically rotates a random token.
        // An unavailable setting fails closed rather than inventing process continuity.
        Settings.Global.getString(context.contentResolver, Settings.Global.BOOT_COUNT)
            ?.takeIf { it.toLongOrNull() != null } ?: throw TrustException()
    },
    System::currentTimeMillis,
    verifier,
    onStoreFailure,
)
