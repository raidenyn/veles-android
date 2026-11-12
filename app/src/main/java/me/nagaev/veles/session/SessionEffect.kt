package me.nagaev.veles.session

/** Effects that a session host turns into transport writes, host callbacks and teardown. */
sealed interface SessionEffect {
    /**
     * Bytes to enqueue. [completionToken] non-null means the engine awaits
     * [PhoneSessionEngine.onSendComplete] for this send before it continues the handshake.
     */
    data class Send(val bytes: ByteArray, val completionToken: Long? = null) : SessionEffect

    /**
     * The authentication gate opened: the class is fixed for the rest of the session.
     *
     * [unprompted] carries the application records this session owes at open (OTP2-STA-010b
     * steps 0 and 5). The engine never sends them itself: the owner applies each one through
     * [PhoneSessionEngine.sendApplication] after it has handled this effect, so no suspend seal
     * runs inside the engine's finality monitor.
     */
    data class Authenticated(
        val sessionClass: SessionClass,
        val peerId: ByteArray,
        val sessionIdPrefix: ByteArray,
        val unprompted: List<UnpromptedApplication> = emptyList(),
    ) : SessionEffect

    /** A verified, authorized application record body for the host. */
    data class Deliver(val type: Int, val body: ByteArray) : SessionEffect

    /** Terminal failure; a host must always follow it with [Close]. */
    data class Terminated(val reason: String) : SessionEffect

    /** Close the connection generation. */
    data object Close : SessionEffect
}

/**
 * The fixed facts of one authenticated session (OTP-10 plan 9b): the class and reported
 * `peer_state` are final at the `Authenticated` gate, and the recovery keys are the live
 * holder the activation rows use.
 *
 * [et] is the public enrollment transcript the record carried, or null. [recovery] is null
 * when the record carried no `ET` AND whenever the class is `full`, because the engine zeroes
 * the holder when it fixes that class (OTP2-CRY-019f); a full session must never hand out
 * recovery keys.
 */
data class SessionContext(
    val sessionIdPrefix: ByteArray,
    val sessionClass: SessionClass,
    val peerId: String,
    val et: ByteArray?,
    val peerState: Int,
    val recovery: RecoveryKeys?,
)

/** One record a handler owes the peer as soon as the session is open (OTP2-STA-010b). */
data class UnpromptedApplication(val type: Int, val body: ByteArray)

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
     *
     * The handler receives [context] on the `Authenticated` effect. From that point the
     * session is open, so the owner may send an unprompted application record through
     * `sendApplication` without waiting for an inbound record (OTP-10 plan 9b).
     */
    fun forSession(context: SessionContext): ApplicationHandler = this

    /**
     * Records this session-scoped handler owes as soon as the gate opens. The engine only
     * reports them on [SessionEffect.Authenticated]; the owner sends each one with
     * `sendApplication` after handling the effect.
     */
    fun unprompted(): List<UnpromptedApplication> = emptyList()
}
