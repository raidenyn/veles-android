import { describe, expect, it } from 'vitest';

import {
    PAIRING_FRAME_HEADER_BYTES,
    PAIRING_FRAME_MAX_BYTES,
    PairingFrameReject,
    PairingFrameType,
    decodePairingFrame,
    encodePairingFrame,
    preauthReject,
} from '../../src/pairing/frames';
import { FrameType } from '../../src/session/wire';

const REQUEST_PAYLOAD = 21;
const OFFER_PAYLOAD = 102;
const POINT_BYTES = 65;
const CONFIRMATION_BYTES = 32;

describe('pairing frames', () => {
    it('round_trips_every_pairing_frame_type', () => {
        const cases: [number, Uint8Array][] = [
            [PairingFrameType.REQUEST, new Uint8Array(REQUEST_PAYLOAD)],
            [PairingFrameType.PA, new Uint8Array(OFFER_PAYLOAD)],
            [PairingFrameType.PB, new Uint8Array(POINT_BYTES)],
            [PairingFrameType.CA, new Uint8Array(CONFIRMATION_BYTES)],
            [PairingFrameType.CB, new Uint8Array(CONFIRMATION_BYTES)],
        ];
        for (const [type, payload] of cases) {
            const frame = encodePairingFrame(type as never, payload);
            expect(frame[0]).toBe(type);
            const decoded = decodePairingFrame(frame);
            expect(decoded.type).toBe(type);
            expect(decoded.payload).toEqual(payload);
        }
    });

    it('uses_types_0x01_through_0x05_and_never_reconnect_types', () => {
        expect(Object.values(PairingFrameType)).toEqual([0x01, 0x02, 0x03, 0x04, 0x05]);
        const reconnect = new Set<number>(Object.values(FrameType));
        for (const code of Object.values(PairingFrameType)) {
            expect(reconnect.has(code)).toBe(false);
        }
    });

    it('emits_the_constant_empty_preauth_reject_frame', () => {
        expect(preauthReject()).toEqual(Uint8Array.of(0x0f, 0x00, 0x00));
        expect(() => decodePairingFrame(preauthReject())).toThrow(PairingFrameReject);
    });

    it('rejects_unknown_reconnect_and_reject_frame_types', () => {
        for (const type of [0x00, 0x06, 0x07, 0x0f, 0xff]) {
            expect(() => decodePairingFrame(Uint8Array.of(type, 0, 0))).toThrow(PairingFrameReject);
        }
    });

    it('rejects_length_disagreements_and_oversize_frames', () => {
        expect(() => decodePairingFrame(Uint8Array.of(0x01, 0, 2, 7))).toThrow(PairingFrameReject);
        expect(() => decodePairingFrame(Uint8Array.of(0x01, 0, 1, 7, 8))).toThrow(
            PairingFrameReject,
        );
        expect(() => decodePairingFrame(Uint8Array.of(0x01, 0))).toThrow(PairingFrameReject);
        expect(() =>
            encodePairingFrame(
                PairingFrameType.REQUEST,
                new Uint8Array(PAIRING_FRAME_MAX_BYTES - PAIRING_FRAME_HEADER_BYTES + 1),
            ),
        ).toThrow(PairingFrameReject);
        expect(() => decodePairingFrame(new Uint8Array(PAIRING_FRAME_MAX_BYTES + 1))).toThrow(
            PairingFrameReject,
        );
        expect(
            encodePairingFrame(
                PairingFrameType.REQUEST,
                new Uint8Array(PAIRING_FRAME_MAX_BYTES - PAIRING_FRAME_HEADER_BYTES),
            ),
        ).toHaveLength(PAIRING_FRAME_MAX_BYTES);
    });

    it('is_byte_identical_to_the_kotlin_codec', () => {
        // `PairingFrames.encode(PB, 65-byte point)` in Kotlin is `03 00 41 || pB`.
        const point = new Uint8Array(POINT_BYTES);
        point[0] = 0x04;
        const frame = encodePairingFrame(PairingFrameType.PB, point);
        expect(frame.subarray(0, 3)).toEqual(Uint8Array.of(0x03, 0x00, 0x41));
        expect(frame.subarray(3)).toEqual(point);
    });
});
