// Wire constants of the Veles reconnect protocol, mirroring the Kotlin
// `me.nagaev.veles.session.Wire` enums. Values are the frozen wire numbers from
// docs/protocol/veles-v1.md sections 4, 7 and 11.

/** Wire roles: the phone is SPAKE2 party A and the client is party B (§3). */
export const Role = {
    PHONE: 0x01,
    CLIENT: 0x02,
} as const;

export type Role = (typeof Role)[keyof typeof Role];

export function roleFromWire(value: number): Role | null {
    return value === Role.PHONE || value === Role.CLIENT ? value : null;
}

/** Protected-record directions (OTP2-CRY-016a). */
export const Direction = {
    CLIENT_TO_PHONE: 0x01,
    PHONE_TO_CLIENT: 0x02,
} as const;

export type Direction = (typeof Direction)[keyof typeof Direction];

export function directionFromWire(value: number): Direction | null {
    return value === Direction.CLIENT_TO_PHONE || value === Direction.PHONE_TO_CLIENT
        ? value
        : null;
}

/** Pre-auth plaintext frame types accepted by the reconnect session (OTP2-ENC-010). */
export const FrameType = {
    RECONNECT_HELLO: 0x06,
    RECONNECT_SIGNATURE: 0x07,
    PREAUTH_REJECT: 0x0f,
} as const;

export type FrameType = (typeof FrameType)[keyof typeof FrameType];

export function frameTypeFromWire(value: number): FrameType | null {
    return value === FrameType.RECONNECT_HELLO ||
        value === FrameType.RECONNECT_SIGNATURE ||
        value === FrameType.PREAUTH_REJECT
        ? value
        : null;
}

/** Protected record types and their §7.3 directions. */
export const RecordType = {
    RECONNECT_CONFIRM: 0x0001,
    APP_HEARTBEAT: 0x0002,
    APP_ERROR: 0x0003,
    ENROLL_IDENTITY: 0x0100,
    ACTIVATION_COMMIT: 0x0101,
    ACTIVATION_COMPLETE: 0x0102,
    ACTIVATION_ACK: 0x0103,
    ENROLLMENT_PREPARE: 0x0104,
    PULL_REQUEST: 0x0200,
    PULL_EVENTS: 0x0202,
    PUSH_EVENT: 0x0203,
    EVENT_ACK: 0x0204,
    REMOVAL_REQUEST: 0x0205,
    REMOVAL_RESPONSE: 0x0206,
    PHONE_POLICY_STATE: 0x0207,
} as const;

export type RecordType = (typeof RecordType)[keyof typeof RecordType];

const CLIENT_TO_PHONE_TYPES: ReadonlySet<number> = new Set([
    RecordType.RECONNECT_CONFIRM,
    RecordType.APP_HEARTBEAT,
    RecordType.APP_ERROR,
    RecordType.ENROLL_IDENTITY,
    RecordType.ACTIVATION_COMMIT,
    RecordType.ACTIVATION_ACK,
    RecordType.PULL_REQUEST,
    RecordType.EVENT_ACK,
    RecordType.REMOVAL_REQUEST,
    RecordType.REMOVAL_RESPONSE,
]);

const PHONE_TO_CLIENT_TYPES: ReadonlySet<number> = new Set([
    RecordType.RECONNECT_CONFIRM,
    RecordType.APP_HEARTBEAT,
    RecordType.APP_ERROR,
    RecordType.ENROLL_IDENTITY,
    RecordType.ACTIVATION_COMPLETE,
    RecordType.ENROLLMENT_PREPARE,
    RecordType.PULL_EVENTS,
    RecordType.PUSH_EVENT,
    RecordType.REMOVAL_REQUEST,
    RecordType.REMOVAL_RESPONSE,
    RecordType.PHONE_POLICY_STATE,
]);

export function recordTypeFromWire(value: number): RecordType | null {
    for (const type of Object.values(RecordType)) {
        if (type === value) return type;
    }
    return null;
}

export function recordTypeAllows(type: RecordType, direction: Direction): boolean {
    return direction === Direction.CLIENT_TO_PHONE
        ? CLIENT_TO_PHONE_TYPES.has(type)
        : PHONE_TO_CLIENT_TYPES.has(type);
}

/** OTP2-ENC-021 application error codes. */
export const AppErrorCode = {
    PROTOCOL_VIOLATION: 0x01,
    RESOURCE_EXHAUSTED: 0x02,
    SESSION_EXPIRED: 0x03,
    ENROLLMENT_REJECTED: 0x04,
    UNAUTHORIZED_RECORD_CLASS: 0x05,
} as const;

export type AppErrorCode = (typeof AppErrorCode)[keyof typeof AppErrorCode];

export function appErrorCodeFromWire(value: number): AppErrorCode | null {
    for (const code of Object.values(AppErrorCode)) {
        if (code === value) return code;
    }
    return null;
}

/** Fixed authorization classes (§11.4). */
export const SessionClass = {
    FULL: 'FULL',
    FULL_PENDING_PEER: 'FULL_PENDING_PEER',
    ACTIVATION_RECOVERY: 'ACTIVATION_RECOVERY',
} as const;

export type SessionClass = (typeof SessionClass)[keyof typeof SessionClass];
