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
    /** OTP2-REC-011 public pair fingerprint display; kept after the window closes. */
    val fingerprint: String? = null,
) {
    override fun toString(): String = "PairingDebugState(open=$open, pairingId=$pairingId, code=REDACTED, fingerprint=$fingerprint)"
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
    private var onCancel: (() -> Unit)? = null
    private var fingerprint: String? = null

    fun attach(window: PairingWindow, onCancel: () -> Unit = {}) {
        synchronized(lock) {
            this.window = window
            this.onCancel = onCancel
            fingerprint = null
        }
        refresh()
    }

    fun detach() {
        synchronized(lock) {
            window = null
            onCancel = null
            fingerprint = null
        }
        mutable.value = PairingDebugState()
    }

    /** Opens the window; false when it is already open or no service has attached one. */
    fun start(): Boolean {
        val opened = synchronized(lock) {
            val current = window
            if (current == null) {
                false
            } else {
                // A successful open is a new pair: the previous pair's public
                // fingerprint is no longer current. A refused, closed or expired
                // window keeps it.
                val result = try {
                    current.open()
                    true
                } catch (_: IllegalStateException) {
                    false
                }
                if (result) fingerprint = null
                result
            }
        }
        refresh()
        return opened
    }

    fun cancel() {
        val (current, callback) = synchronized(lock) { window to onCancel }
        current?.cancel()
        callback?.invoke()
        refresh()
    }

    /**
     * OTP2-REC-011: publish the public fingerprint once both identities are authenticated.
     * The window may already be closed by the verified `cB`; the value stays on the state flow
     * until a new window is attached or the service detaches.
     */
    fun publishFingerprint(value: String) {
        synchronized(lock) {
            if (window == null) return
            fingerprint = value
            mutable.value = mutable.value.copy(fingerprint = value)
        }
    }

    /** Re-reads the lazily expiring window so an expired window reaches the StateFlow. */
    fun refresh() {
        synchronized(lock) {
            // Everything, including publishing, happens under the attach/detach lock: a
            // refresh that raced detach() can never republish an open state afterwards.
            val current = window
            if (current == null || !current.isOpen()) {
                mutable.value = PairingDebugState(fingerprint = fingerprint)
                return
            }
            val digits = try {
                current.codeBytes()
            } catch (_: IllegalStateException) {
                mutable.value = PairingDebugState(fingerprint = fingerprint)
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
                fingerprint = fingerprint,
            )
        }
    }
}
