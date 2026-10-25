package me.nagaev.veles.trust

import android.content.Context
import android.os.SystemClock
import android.provider.Settings

/** Caller supplies the authenticated session's activation verifier (OTP-09/10).
 * There is deliberately no accept-all verifier or UI activation shortcut.
 */
fun createAndroidTrustRepository(context: Context, verifier: ActivationVerifier): TrustRepository =
    TrustRepository(
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
    )
