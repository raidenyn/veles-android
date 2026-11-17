package me.nagaev.veles.debugprobe

import android.content.Context
import android.os.PowerManager

/**
 * The partial wake lock the scheduler holds while a push is pending (OTP-12 design D4). It is a
 * non-reference-counted lock acquired with a timed backstop of the delay plus 60 s, so the OS
 * releases it even if the process never reaches the scheduler's own release. Releasing after that
 * timed acquire expired is safe: [PowerManager.WakeLock.isHeld] is false then, and a blind
 * `release()` would be an under-lock.
 *
 * The tag keeps the `me.nagaev.veles.debug` namespace but uses a colon because lint requires the
 * platform's `prefix:tag` wake-lock convention. That places it outside the dotted
 * `me.nagaev.veles.debug.` release-guard prefix, so `releaseHookForbidden` names the exact tag.
 * The class exists only in the debug source set.
 */
internal class PowerManagerWakeResource internal constructor(
    private val wakeLock: PowerManager.WakeLock,
) : WakeResource {
    constructor(context: Context) : this(
        context.getSystemService(PowerManager::class.java)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, TAG),
    )

    init {
        wakeLock.setReferenceCounted(false)
    }

    @Synchronized
    override fun acquire(timeoutMillis: Long) {
        if (!wakeLock.isHeld) wakeLock.acquire(timeoutMillis)
    }

    @Synchronized
    override fun release() {
        if (wakeLock.isHeld) wakeLock.release()
    }

    internal companion object {
        internal const val TAG = "me.nagaev.veles.debug:GateBPush"
    }
}
