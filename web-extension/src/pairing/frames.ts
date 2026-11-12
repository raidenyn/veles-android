// OTP2-ENC-010 pairing frame codec. Byte-identical to the Kotlin
// `me.nagaev.veles.pairing.PairingFrames`: `type u8 || payload_len u16be || payload`,
// at most 4096 bytes total, types 0x01-0x05, and the constant empty
// `preauth_reject` (0x0F) from OTP2-ENC-010e.
//
// This is deliberately separate from the reconnect `FrameType` set in
// `session/wire.ts` (0x06, 0x07, 0x0F): pairing has its own enum and allow-list,
// and decoding pairing frames never accepts a reconnect frame.

export const PAIRING_FRAME_HEADER_BYTES = 3;
export const PAIRING_FRAME_MAX_BYTES = 4096;
export const PREAUTH_REJECT = 0x0f;

const U8_MASK = 0xff;
const BITS_PER_BYTE = 8;
const HIGH_BYTE_SHIFT = 8;

/** OTP2-ENC-010 pairing frame types (0x01-0x05). */
export const PairingFrameType = {
    REQUEST: 0x01,
    PA: 0x02,
    PB: 0x03,
    CA: 0x04,
    CB: 0x05,
} as const;

export type PairingFrameType = (typeof PairingFrameType)[keyof typeof PairingFrameType];

export interface PairingFrame {
    type: PairingFrameType;
    payload: Uint8Array;
}

/** Malformed pairing frame. It carries no message and never echoes payload bytes. */
export class PairingFrameReject extends Error {
    constructor() {
        super();
        this.name = 'PairingFrameReject';
    }
}

function reject(): never {
    throw new PairingFrameReject();
}

function pairingFrameTypeFromWire(value: number): PairingFrameType | null {
    for (const type of Object.values(PairingFrameType)) {
        if (type === value) return type;
    }
    return null;
}

/**
 * The constant empty-payload `preauth_reject` frame (OTP2-ENC-010e). Pairing
 * failures emit exactly these bytes, the same ones the Kotlin codec emits, so no
 * reason code and no payload can cross the wire.
 */
export function preauthReject(): Uint8Array {
    return Uint8Array.of(PREAUTH_REJECT, 0x00, 0x00);
}

export function encodePairingFrame(type: PairingFrameType, payload: Uint8Array): Uint8Array {
    if (payload.length > PAIRING_FRAME_MAX_BYTES - PAIRING_FRAME_HEADER_BYTES) reject();
    const frame = new Uint8Array(PAIRING_FRAME_HEADER_BYTES + payload.length);
    frame[0] = type;
    frame[1] = (payload.length >>> HIGH_BYTE_SHIFT) & U8_MASK;
    frame[2] = payload.length & U8_MASK;
    frame.set(payload, PAIRING_FRAME_HEADER_BYTES);
    return frame;
}

export function decodePairingFrame(frame: Uint8Array): PairingFrame {
    if (frame.length < PAIRING_FRAME_HEADER_BYTES || frame.length > PAIRING_FRAME_MAX_BYTES) {
        reject();
    }
    const type = pairingFrameTypeFromWire(frame[0]);
    if (type === null) reject();
    const payloadLength = ((frame[1] & U8_MASK) << BITS_PER_BYTE) | (frame[2] & U8_MASK);
    if (payloadLength !== frame.length - PAIRING_FRAME_HEADER_BYTES) reject();
    return { type, payload: frame.slice(PAIRING_FRAME_HEADER_BYTES) };
}
