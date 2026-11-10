import { Direction, RecordType, SessionClass, recordTypeAllows, recordTypeFromWire } from './wire';

// The §7.3 record-direction table and the §11.4 authorization classes, a direct
// port of the Kotlin `RecordPolicy`.
//
// The engine fixes one `SessionClass` per authenticated session and must accept
// or send only records that the class authorizes. Framework records
// (`reconnect_confirm`, `app_heartbeat`, `app_error`) are class-independent;
// `enroll_identity` belongs to the enrollment session, which this engine never
// runs, so it is never authorized here.

/** True when `type` may travel in `direction` on a session of class `sessionClass`. */
export function authorized(
    type: number,
    direction: Direction,
    sessionClass: SessionClass,
): boolean {
    const recordType = recordTypeFromWire(type);
    if (recordType === null) return false;
    if (!recordTypeAllows(recordType, direction)) return false;
    switch (recordType) {
        case RecordType.RECONNECT_CONFIRM:
        case RecordType.APP_HEARTBEAT:
        case RecordType.APP_ERROR:
            return true;
        case RecordType.ENROLL_IDENTITY:
            return false;
        case RecordType.ENROLLMENT_PREPARE:
        case RecordType.ACTIVATION_COMMIT:
        case RecordType.ACTIVATION_COMPLETE:
        case RecordType.ACTIVATION_ACK:
            return sessionClass !== SessionClass.FULL;
        case RecordType.PULL_REQUEST:
        case RecordType.PULL_EVENTS:
        case RecordType.PUSH_EVENT:
        case RecordType.EVENT_ACK:
        case RecordType.REMOVAL_REQUEST:
        case RecordType.REMOVAL_RESPONSE:
        case RecordType.PHONE_POLICY_STATE:
            return sessionClass === SessionClass.FULL;
        default:
            return false;
    }
}
