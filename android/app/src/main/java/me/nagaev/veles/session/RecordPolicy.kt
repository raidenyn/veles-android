package me.nagaev.veles.session

/**
 * The §7.3 record-direction table and the §11.4 authorization classes.
 *
 * The phone engine fixes one [SessionClass] per authenticated session and must accept or send
 * only records that the class authorizes. Framework records (`reconnect_confirm`,
 * `app_heartbeat`, `app_error`) are class-independent; `enroll_identity` belongs to the
 * enrollment session, which this engine never runs, so it is never authorized here.
 */
object RecordPolicy {
    /** True when [type] may travel in [direction] on a session of class [cls]. */
    fun authorized(type: Int, direction: Direction, cls: SessionClass): Boolean {
        val recordType = RecordType.fromWire(type) ?: return false
        if (!recordType.allows(direction)) return false
        return when (recordType) {
            RecordType.RECONNECT_CONFIRM, RecordType.APP_HEARTBEAT, RecordType.APP_ERROR -> true
            RecordType.ENROLL_IDENTITY -> false
            RecordType.ENROLLMENT_PREPARE, RecordType.ACTIVATION_COMMIT,
            RecordType.ACTIVATION_COMPLETE, RecordType.ACTIVATION_ACK,
            -> cls != SessionClass.FULL
            RecordType.PULL_REQUEST, RecordType.PULL_EVENTS, RecordType.PUSH_EVENT, RecordType.EVENT_ACK,
            RecordType.REMOVAL_REQUEST, RecordType.REMOVAL_RESPONSE, RecordType.PHONE_POLICY_STATE,
            -> cls == SessionClass.FULL
        }
    }
}
