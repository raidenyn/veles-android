import { requireValidPointBytes } from './p256';
import { SessionReject } from './reject';
import { Role, roleFromWire } from './wire';

// OTP2-REC-020 reconnect hello payload (frame `0x06`):
// `installation_id[16] || ephemeral_pub[65] || nonce[16] || role u8 ||
//  protocol_version u16be || caps_len u16be || caps`.

const INSTALLATION_BYTES = 16;
const POINT_BYTES = 65;
const NONCE_BYTES = 16;
const NONCE_OFFSET = INSTALLATION_BYTES + POINT_BYTES;
const ROLE_OFFSET = NONCE_OFFSET + NONCE_BYTES;
const VERSION_OFFSET = ROLE_OFFSET + 1;
const CAPS_LEN_OFFSET = VERSION_OFFSET + 2;
const FIXED_BYTES = CAPS_LEN_OFFSET + 2;

const PROTOCOL_VERSION = 1;
const MAX_CAPS = 16;
const MANDATORY_CAP = 0x01;
const U8_MASK = 0xff;
const BITS_PER_BYTE = 8;

/** Capabilities this endpoint offers: only the mandatory `VELES_RECORD_V1`. */
export const LOCAL_CAPS: Uint8Array = Uint8Array.of(MANDATORY_CAP);

const KNOWN_CAPABILITIES: Uint8Array = Uint8Array.of(0x01, 0x02, 0x03);

export interface ReconnectHello {
    installationId: Uint8Array;
    ephemeralPublicKey: Uint8Array;
    nonce: Uint8Array;
    role: Role;
    caps: Uint8Array;
}

function reject(): never {
    throw new SessionReject();
}

function u16(value: Uint8Array, offset: number): number {
    return ((value[offset] & U8_MASK) << BITS_PER_BYTE) | (value[offset + 1] & U8_MASK);
}

function writeU16(value: Uint8Array, offset: number, number: number): void {
    value[offset] = (number >>> BITS_PER_BYTE) & U8_MASK;
    value[offset + 1] = number & U8_MASK;
}

function requireCaps(caps: Uint8Array, known: Uint8Array): void {
    if (caps.length === 0 || caps.length > MAX_CAPS) reject();
    if (caps[0] !== MANDATORY_CAP) reject();
    let previous = -1;
    for (const cap of caps) {
        const value = cap & U8_MASK;
        if (value <= previous) reject();
        if (!known.includes(value)) reject();
        previous = value;
    }
}

export function encodeHello(hello: ReconnectHello): Uint8Array {
    if (
        hello.installationId.length !== INSTALLATION_BYTES ||
        hello.ephemeralPublicKey.length !== POINT_BYTES ||
        hello.nonce.length !== NONCE_BYTES
    ) {
        reject();
    }
    requireCaps(hello.caps, KNOWN_CAPABILITIES);
    requireValidPointBytes(hello.ephemeralPublicKey);

    const payload = new Uint8Array(FIXED_BYTES + hello.caps.length);
    payload.set(hello.installationId, 0);
    payload.set(hello.ephemeralPublicKey, INSTALLATION_BYTES);
    payload.set(hello.nonce, NONCE_OFFSET);
    payload[ROLE_OFFSET] = hello.role;
    writeU16(payload, VERSION_OFFSET, PROTOCOL_VERSION);
    writeU16(payload, CAPS_LEN_OFFSET, hello.caps.length);
    payload.set(hello.caps, FIXED_BYTES);
    return payload;
}

export function decodeHello(
    payload: Uint8Array,
    expectedRole: Role,
    knownCaps: Uint8Array = KNOWN_CAPABILITIES,
): ReconnectHello {
    if (payload.length < FIXED_BYTES) reject();

    const role = roleFromWire(payload[ROLE_OFFSET]);
    if (role === null || role !== expectedRole) reject();
    if (u16(payload, VERSION_OFFSET) !== PROTOCOL_VERSION) reject();

    const capsLength = u16(payload, CAPS_LEN_OFFSET);
    if (payload.length !== FIXED_BYTES + capsLength) reject();
    const caps = payload.slice(FIXED_BYTES);
    requireCaps(caps, knownCaps);

    const ephemeralPublicKey = payload.slice(INSTALLATION_BYTES, INSTALLATION_BYTES + POINT_BYTES);
    requireValidPointBytes(ephemeralPublicKey);

    return {
        installationId: payload.slice(0, INSTALLATION_BYTES),
        ephemeralPublicKey,
        nonce: payload.slice(NONCE_OFFSET, NONCE_OFFSET + NONCE_BYTES),
        role,
        caps,
    };
}
