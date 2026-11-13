package me.nagaev.veles.pairing

/**
 * Owns exactly one core handle for one pairing attempt.
 *
 * [close] is idempotent and safe in a `finally`: it releases the handle without
 * replacing the exception that caused the exit. Consumers that follow the happy
 * path still call `finish`, which consumes the handle inside the core; the later
 * [close] then only drops the already-absent entry.
 */
class PairingExecution(
    private val core: PairingCore,
    val handle: Handle,
) : AutoCloseable {
    private var closed = false

    val isClosed: Boolean get() = closed

    fun exchange(peerPoint: ByteArray): ByteArray = alive().exchange(handle, peerPoint)

    fun confirm(peerConfirmation: ByteArray): ByteArray = alive().confirm(handle, peerConfirmation)

    fun finish(): ByteArray = alive().finish(handle)

    override fun close() {
        if (closed) return
        closed = true
        try {
            core.close(handle)
        } catch (_: Exception) {
            // Cleanup must not mask the caller's real outcome; the handle is released
            // either way by the core's close-on-every-exit contract.
        }
    }

    private fun alive(): PairingCore {
        check(!closed) { "pairing execution is closed" }
        return core
    }
}
