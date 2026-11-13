package me.nagaev.veles.pairing

import android.os.SystemClock
import java.security.SecureRandom

/** The result of [PairingWindow.open]: the wire id and the in-memory code to show. */
data class Opened(val pairingId: ByteArray, val code: PairingCode)

/**
 * The phone's single pairing window.
 *
 * At most one window is open; it lasts 300 s on the injected monotonic clock
 * (convention: [SystemClock.elapsedRealtime]). Expiry is checked lazily on every
 * access, so every observation and every password-sensitive deadline check after
 * 300 s sees a closed window with an erased code. Nothing is persisted: a process
 * restart starts with no window and no code by construction.
 */
class PairingWindow(
    private val clock: () -> Long = SystemClock::elapsedRealtime,
    private val random: SecureRandom = SecureRandom(),
) {
    private val lock = Any()
    private var openedAt = 0L
    private var id: ByteArray? = null
    private var code: PairingCode? = null

    /** True while a window is open; a window at or past 300 s is closed first. */
    fun isOpen(): Boolean = synchronized(lock) {
        expireIfNeeded(clock())
        code != null
    }

    /** The current 128-bit pairing id, or an empty array when no window is open. */
    val pairingId: ByteArray
        get() = synchronized(lock) {
            expireIfNeeded(clock())
            id?.copyOf() ?: ByteArray(0)
        }

    /**
     * Opens the window and draws a fresh 128-bit id and six-digit code.
     *
     * A second open while the window is live is refused without drawing anything or
     * changing the open window.
     */
    fun open(): Opened = synchronized(lock) {
        val now = clock()
        expireIfNeeded(now)
        check(code == null) { "pairing window already open" }
        val pairingId = ByteArray(PAIRING_ID_BYTES).also(random::nextBytes)
        val pairingCode = PairingCode.draw(random)
        id = pairingId
        code = pairingCode
        openedAt = now
        Opened(pairingId.copyOf(), pairingCode)
    }

    /**
     * The six ASCII digits, handed only to the core's `start`. Throws when no window
     * is open (including after expiry or cancel).
     */
    fun codeBytes(): ByteArray = synchronized(lock) {
        expireIfNeeded(clock())
        val current = code ?: error("pairing window is not open")
        current.copyBytes()
    }

    /** Closes the window early and erases the code. Cancelling a closed window is a no-op. */
    fun cancel() = synchronized(lock) {
        clear()
    }

    private fun expireIfNeeded(now: Long) {
        if (code == null) return
        if (now - openedAt >= WINDOW_MILLIS) clear()
    }

    private fun clear() {
        code?.erase()
        code = null
        id?.fill(0)
        id = null
        openedAt = 0L
    }

    private companion object {
        const val WINDOW_MILLIS = 300_000L
        const val PAIRING_ID_BYTES = 16
    }
}
