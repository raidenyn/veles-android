package me.nagaev.veles.session

/** Wire roles: the phone is SPAKE2 party A and the client is party B (veles-v1.md §3). */
enum class Role(val wire: Int) {
    PHONE(0x01),
    CLIENT(0x02),
    ;

    companion object {
        fun fromWire(value: Int): Role? = entries.firstOrNull { it.wire == value }
    }
}

/** Protected-record directions (OTP2-CRY-016a). */
enum class Direction(val wire: Int) {
    CLIENT_TO_PHONE(0x01),
    PHONE_TO_CLIENT(0x02),
    ;

    companion object {
        fun fromWire(value: Int): Direction? = entries.firstOrNull { it.wire == value }
    }
}

/** Pre-auth plaintext frame types accepted by the reconnect session (OTP2-ENC-010). */
enum class FrameType(val wire: Int) {
    RECONNECT_HELLO(0x06),
    RECONNECT_SIGNATURE(0x07),
    PREAUTH_REJECT(0x0F),
    ;

    companion object {
        fun fromWire(value: Int): FrameType? = entries.firstOrNull { it.wire == value }
    }
}

/** Protected record types and their §7.3 directions. */
enum class RecordType(
    val wire: Int,
    val clientToPhone: Boolean,
    val phoneToClient: Boolean,
) {
    RECONNECT_CONFIRM(0x0001, true, true),
    APP_HEARTBEAT(0x0002, true, true),
    APP_ERROR(0x0003, true, true),
    ENROLL_IDENTITY(0x0100, true, true),
    ACTIVATION_COMMIT(0x0101, true, false),
    ACTIVATION_COMPLETE(0x0102, false, true),
    ACTIVATION_ACK(0x0103, true, false),
    ENROLLMENT_PREPARE(0x0104, false, true),
    PULL_REQUEST(0x0200, true, false),
    PULL_EVENTS(0x0202, false, true),
    PUSH_EVENT(0x0203, false, true),
    EVENT_ACK(0x0204, true, false),
    REMOVAL_REQUEST(0x0205, true, true),
    REMOVAL_RESPONSE(0x0206, true, true),
    PHONE_POLICY_STATE(0x0207, false, true),
    ;

    fun allows(direction: Direction): Boolean = when (direction) {
        Direction.CLIENT_TO_PHONE -> clientToPhone
        Direction.PHONE_TO_CLIENT -> phoneToClient
    }

    companion object {
        fun fromWire(value: Int): RecordType? = entries.firstOrNull { it.wire == value }
    }
}

/** OTP2-ENC-021 application error codes. */
enum class AppErrorCode(val wire: Int) {
    PROTOCOL_VIOLATION(0x01),
    RESOURCE_EXHAUSTED(0x02),
    SESSION_EXPIRED(0x03),
    ENROLLMENT_REJECTED(0x04),
    UNAUTHORIZED_RECORD_CLASS(0x05),
    ;

    companion object {
        fun fromWire(value: Int): AppErrorCode? = entries.firstOrNull { it.wire == value }
    }
}

/** Fixed authorization classes (veles-v1.md §11.4). */
enum class SessionClass { FULL, FULL_PENDING_PEER, ACTIVATION_RECOVERY }
