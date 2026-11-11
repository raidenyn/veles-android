package me.nagaev.veles.session

/** Effects that a session host turns into transport writes, host callbacks and teardown. */
sealed interface SessionEffect {
    /**
     * Bytes to enqueue. [completionToken] non-null means the engine awaits
     * [PhoneSessionEngine.onSendComplete] for this send before it continues the handshake.
     */
    data class Send(val bytes: ByteArray, val completionToken: Long? = null) : SessionEffect

    /** The authentication gate opened: the class is fixed for the rest of the session. */
    data class Authenticated(
        val sessionClass: SessionClass,
        val peerId: ByteArray,
        val sessionIdPrefix: ByteArray,
    ) : SessionEffect

    /** A verified, authorized application record body for the host. */
    data class Deliver(val type: Int, val body: ByteArray) : SessionEffect

    /** Terminal failure; a host must always follow it with [Close]. */
    data class Terminated(val reason: String) : SessionEffect

    /** Close the connection generation. */
    data object Close : SessionEffect
}

/**
 * Application records that have a handler. An authorized record without a handler fails
 * closed (OTP2 protected-record-session "Authorized records without a handler fail closed").
 */
interface ApplicationHandler {
    fun handles(type: Int): Boolean
    suspend fun handle(type: Int, body: ByteArray, reply: suspend (type: Int, body: ByteArray) -> Unit)

    /**
     * Optional per-session context: the engine asks for the handler instance of one
     * authenticated session once, when the class is fixed, and uses only that instance for
     * the session's records. The default keeps this same instance.
     */
    fun forSession(sessionIdPrefix: ByteArray): ApplicationHandler = this
}
