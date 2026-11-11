import { SessionReject } from './reject';
import {
    Direction,
    RecordType,
    appErrorCodeFromWire,
    recordTypeAllows,
    recordTypeFromWire,
} from './wire';

// §7 protected record format: 23-byte header (also the AAD) || AES-256-GCM
// ciphertext || tag, nonce `prefix[4] || u64be(sequence)`, at most 4096 bytes
// per record.

export const RECORD_HEADER_BYTES = 23;
export const RECORD_TAG_BYTES = 16;
export const RECORD_MAX_BYTES = 4096;
export const RECORD_MAX_CIPHERTEXT_BYTES = 4057;

const PROTOCOL_VERSION = 1;
const SCHEMA_VERSION = 1;
const SESSION_PREFIX_BYTES = 8;
const NONCE_PREFIX_BYTES = 4;
const NONCE_BYTES = 12;
const GCM_TAG_BITS = 128;
const U8_MASK = 0xff;
const BITS_PER_BYTE = 8;

const TYPE_OFFSET = 3;
const SEQUENCE_OFFSET = 5;
const LENGTH_OFFSET = 13;
const SESSION_OFFSET = 15;

const APP_ERROR_HEADER_BYTES = 3;
const APP_ERROR_MAX_REASON = 64;
const CONFIRM_BODY_BYTES = 33;
const MAC_BODY_BYTES = 32;
const ENROLL_IDENTITY_MIN = 132;
const ENROLL_IDENTITY_MAX = 164;
const POLICY_STATE_BYTES = 2;
const POLICY_ALWAYS = 2;
const POLICY_RESERVED_MASK = 0xf0;

const utf8Decoder = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });

/** A verified and decrypted protected record. */
export interface OpenedRecord {
    type: number;
    seq: bigint;
    body: Uint8Array;
}

function reject(): never {
    throw new SessionReject();
}

function u8(value: Uint8Array, offset: number): number {
    return value[offset] & U8_MASK;
}

function u16(value: Uint8Array, offset: number): number {
    return (u8(value, offset) << BITS_PER_BYTE) | u8(value, offset + 1);
}

function writeU16(value: Uint8Array, offset: number, number: number): void {
    value[offset] = (number >>> BITS_PER_BYTE) & U8_MASK;
    value[offset + 1] = number & U8_MASK;
}

function writeU64(value: Uint8Array, offset: number, sequence: bigint): void {
    if (sequence < 0n || sequence > 0xffffffffffffffffn) reject();
    let remaining = sequence;
    for (let index = offset + 7; index >= offset; index -= 1) {
        value[index] = Number(remaining & 0xffn);
        remaining >>= 8n;
    }
}

function readU64(value: Uint8Array, offset: number): bigint {
    let sequence = 0n;
    for (let index = offset; index < offset + 8; index += 1) {
        sequence = (sequence << 8n) | BigInt(u8(value, index));
    }
    return sequence;
}

function requireKeyMaterial(prefix: Uint8Array, sessionIdPrefix: Uint8Array): void {
    if (prefix.length !== NONCE_PREFIX_BYTES) reject();
    if (sessionIdPrefix.length !== SESSION_PREFIX_BYTES) reject();
}

function requireType(type: number, direction: Direction): void {
    const recordType = recordTypeFromWire(type);
    if (recordType === null || !recordTypeAllows(recordType, direction)) reject();
}

function header(
    direction: Direction,
    type: number,
    sequence: bigint,
    ciphertextLength: number,
    sessionIdPrefix: Uint8Array,
): Uint8Array {
    const value = new Uint8Array(RECORD_HEADER_BYTES);
    value[0] = PROTOCOL_VERSION;
    value[1] = SCHEMA_VERSION;
    value[2] = direction;
    writeU16(value, TYPE_OFFSET, type);
    writeU64(value, SEQUENCE_OFFSET, sequence);
    writeU16(value, LENGTH_OFFSET, ciphertextLength);
    value.set(sessionIdPrefix, SESSION_OFFSET);
    return value;
}

function nonce(prefix: Uint8Array, sequence: bigint): Uint8Array {
    const value = new Uint8Array(NONCE_BYTES);
    value.set(prefix, 0);
    writeU64(value, NONCE_PREFIX_BYTES, sequence);
    return value;
}

/**
 * The fixed body rules of the §7.3 table, applied to every seal and every successful open:
 * empty bodies, fixed widths, `enroll_identity` bounds, the `phone_policy_state` reserved bits,
 * and OTP2-ENC-003 bounded safe text for the `app_error` reason. Variable-length application
 * payloads (`pull_events`, `push_event`) stay with their handlers, which own semantic event
 * parsing.
 */
export function requireBody(type: number, body: Uint8Array): void {
    switch (type) {
        case RecordType.APP_HEARTBEAT:
        case RecordType.PULL_REQUEST:
        case RecordType.REMOVAL_REQUEST:
        case RecordType.REMOVAL_RESPONSE:
            if (body.length !== 0) reject();
            return;
        case RecordType.RECONNECT_CONFIRM:
            if (body.length !== CONFIRM_BODY_BYTES) reject();
            return;
        case RecordType.ACTIVATION_COMMIT:
        case RecordType.ACTIVATION_COMPLETE:
        case RecordType.ACTIVATION_ACK:
        case RecordType.ENROLLMENT_PREPARE:
        case RecordType.EVENT_ACK:
            if (body.length !== MAC_BODY_BYTES) reject();
            return;
        case RecordType.ENROLL_IDENTITY:
            if (body.length < ENROLL_IDENTITY_MIN || body.length > ENROLL_IDENTITY_MAX) reject();
            return;
        case RecordType.APP_ERROR: {
            if (body.length < APP_ERROR_HEADER_BYTES) reject();
            if (appErrorCodeFromWire(u8(body, 0)) === null) reject();
            const reasonLength = u16(body, 1);
            if (reasonLength > APP_ERROR_MAX_REASON) reject();
            if (body.length !== APP_ERROR_HEADER_BYTES + reasonLength) reject();
            let reason: string;
            try {
                reason = utf8Decoder.decode(body.subarray(APP_ERROR_HEADER_BYTES));
            } catch {
                reject();
            }
            // OTP2-ENC-003: no U+0000..U+001F and no U+007F in any Veles string.
            if (
                Array.from(reason).some(
                    (char) => char.charCodeAt(0) < 0x20 || char.charCodeAt(0) === 0x7f,
                )
            ) {
                reject();
            }
            return;
        }
        case RecordType.PHONE_POLICY_STATE:
            if (body.length !== POLICY_STATE_BYTES) reject();
            if (u8(body, 0) > POLICY_ALWAYS) reject();
            if ((u8(body, 1) & POLICY_RESERVED_MASK) !== 0) reject();
            return;
        default:
            // `pull_events` and `push_event` payloads are parsed by their handlers.
            return;
    }
}

export async function sealRecord(
    key: CryptoKey,
    prefix: Uint8Array,
    sessionIdPrefix: Uint8Array,
    direction: Direction,
    type: number,
    sequence: bigint,
    body: Uint8Array,
): Promise<Uint8Array> {
    requireKeyMaterial(prefix, sessionIdPrefix);
    requireType(type, direction);
    requireBody(type, body);
    if (body.length > RECORD_MAX_CIPHERTEXT_BYTES) reject();

    const aad = header(direction, type, sequence, body.length, sessionIdPrefix);
    if (RECORD_HEADER_BYTES + body.length + RECORD_TAG_BYTES > RECORD_MAX_BYTES) reject();

    let ciphertext: Uint8Array;
    try {
        ciphertext = new Uint8Array(
            await crypto.subtle.encrypt(
                {
                    name: 'AES-GCM',
                    iv: nonce(prefix, sequence),
                    additionalData: aad,
                    tagLength: GCM_TAG_BITS,
                },
                key,
                body,
            ),
        );
    } catch {
        return reject();
    }

    const record = new Uint8Array(aad.length + ciphertext.length);
    record.set(aad, 0);
    record.set(ciphertext, aad.length);
    if (record.length > RECORD_MAX_BYTES) reject();
    return record;
}

export async function openRecord(
    key: CryptoKey,
    prefix: Uint8Array,
    sessionIdPrefix: Uint8Array,
    direction: Direction,
    expectedSeq: bigint,
    record: Uint8Array,
): Promise<OpenedRecord> {
    requireKeyMaterial(prefix, sessionIdPrefix);
    if (record.length < RECORD_HEADER_BYTES + RECORD_TAG_BYTES) reject();
    if (record.length > RECORD_MAX_BYTES) reject();
    if (u8(record, 0) !== PROTOCOL_VERSION || u8(record, 1) !== SCHEMA_VERSION) reject();
    if (u8(record, 2) !== direction) reject();

    const type = u16(record, TYPE_OFFSET);
    requireType(type, direction);

    const sequence = readU64(record, SEQUENCE_OFFSET);
    if (sequence !== expectedSeq) reject();

    const ciphertextLength = u16(record, LENGTH_OFFSET);
    if (ciphertextLength > RECORD_MAX_CIPHERTEXT_BYTES) reject();
    if (record.length !== RECORD_HEADER_BYTES + ciphertextLength + RECORD_TAG_BYTES) reject();

    for (let index = 0; index < SESSION_PREFIX_BYTES; index += 1) {
        if (record[SESSION_OFFSET + index] !== sessionIdPrefix[index]) reject();
    }

    const aad = record.slice(0, RECORD_HEADER_BYTES);
    let body: Uint8Array;
    try {
        body = new Uint8Array(
            await crypto.subtle.decrypt(
                {
                    name: 'AES-GCM',
                    iv: nonce(prefix, sequence),
                    additionalData: aad,
                    tagLength: GCM_TAG_BITS,
                },
                key,
                record.subarray(RECORD_HEADER_BYTES),
            ),
        );
    } catch {
        return reject();
    }

    requireBody(type, body);
    return { type, seq: sequence, body };
}
