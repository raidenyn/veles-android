package me.nagaev.veles.debugpairing

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import me.nagaev.veles.pairing.PairingWindow
import me.nagaev.veles.trust.TrustEncoding

/**
 * The debug screen's view of the phone's single pairing window. [code] is the only copy of
 * the six-digit code outside [PairingWindow]; it exists for the on-screen readout and the
 * instrumented tests and is never written to a log, broadcast result, notification or view
 * model. [toString] is redacted because a debug dump or an accidental string interpolation
 * must not reveal it. The closed state replaces it on the next refresh or debug action; the
 * screen refreshes once a second.
 */
data class PairingDebugState(
    val open: Boolean = false,
    val pairingId: String? = null,
    val code: String? = null,
) {
    override fun toString(): String = "PairingDebugState(open=$open, pairingId=$pairingId, code=REDACTED)"
}

/**
 * In-process debug hook (design D5). The production service attaches its window through the
 * debug build-type seam; a release build links neither this object nor the screen.
 */
object PairingDebugHooks {
    private val mutable = MutableStateFlow(PairingDebugState())
    val state: StateFlow<PairingDebugState> = mutable.asStateFlow()

    private val lock = Any()
    private var window: PairingWindow? = null

    fun attach(window: PairingWindow) {
        synchronized(lock) { this.window = window }
        refresh()
    }

    fun detach() {
        synchronized(lock) { window = null }
        mutable.value = PairingDebugState()
    }

    /** Opens the window; false when it is already open or no service has attached one. */
    fun start(): Boolean {
        val current = synchronized(lock) { window } ?: return false
        val opened = try {
            current.open()
            true
        } catch (_: IllegalStateException) {
            false
        }
        refresh()
        return opened
    }

    fun cancel() {
        synchronized(lock) { window }?.cancel()
        refresh()
    }

    /** Re-reads the lazily expiring window so an expired window reaches the StateFlow. */
    fun refresh() {
        synchronized(lock) {
            // Everything, including publishing, happens under the attach/detach lock: a
            // refresh that raced detach() can never republish an open state afterwards.
            val current = window
            if (current == null || !current.isOpen()) {
                mutable.value = PairingDebugState()
                return
            }
            val digits = try {
                current.codeBytes()
            } catch (_: IllegalStateException) {
                mutable.value = PairingDebugState()
                return
            }
            val code = try {
                String(digits, Charsets.US_ASCII)
            } finally {
                digits.fill(0)
            }
            mutable.value = PairingDebugState(
                open = true,
                pairingId = TrustEncoding.hex(current.pairingId),
                code = code,
            )
        }
    }
}
