import { SessionReject } from './reject';
import { FrameType, Role, frameTypeFromWire, roleFromWire } from './wire';

// §4 pre-auth plaintext framing: `type u8 || payload_len u16be || payload`, at
// most 4096 bytes. Signature payloads are `role u8 || signature[64]`.

export const PRE_AUTH_HEADER_BYTES = 3;
export const PRE_AUTH_MAX_FRAME = 4096;
export const SIGNATURE_BYTES = 64;

const U8_MASK = 0xff;
const BITS_PER_BYTE = 8;

export interface SignatureFrame {
    role: Role;
    signature: Uint8Array;
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

export function encodeFrame(type: number, payload: Uint8Array): Uint8Array {
    if (frameTypeFromWire(type) === null) reject();
    if (type === FrameType.PREAUTH_REJECT && payload.length !== 0) reject();
    if (payload.length > PRE_AUTH_MAX_FRAME - PRE_AUTH_HEADER_BYTES) reject();

    const frame = new Uint8Array(PRE_AUTH_HEADER_BYTES + payload.length);
    frame[0] = type;
    writeU16(frame, 1, payload.length);
    frame.set(payload, PRE_AUTH_HEADER_BYTES);
    return frame;
}

export function decodeFrame(frame: Uint8Array): { type: number; payload: Uint8Array } {
    if (frame.length < PRE_AUTH_HEADER_BYTES || frame.length > PRE_AUTH_MAX_FRAME) reject();
    const type = frame[0];
    if (frameTypeFromWire(type) === null) reject();
    const payloadLength = u16(frame, 1);
    if (payloadLength !== frame.length - PRE_AUTH_HEADER_BYTES) reject();
    if (type === FrameType.PREAUTH_REJECT && payloadLength !== 0) reject();
    return { type, payload: frame.slice(PRE_AUTH_HEADER_BYTES) };
}

export function encodeSignatureFrame(frame: SignatureFrame): Uint8Array {
    if (frame.signature.length !== SIGNATURE_BYTES) reject();
    const payload = new Uint8Array(1 + SIGNATURE_BYTES);
    payload[0] = frame.role;
    payload.set(frame.signature, 1);
    return payload;
}

export function decodeSignatureFrame(payload: Uint8Array, expectedRole: Role): SignatureFrame {
    if (payload.length !== 1 + SIGNATURE_BYTES) reject();
    const role = roleFromWire(payload[0]);
    if (role === null || role !== expectedRole) reject();
    return { role, signature: payload.slice(1) };
}
