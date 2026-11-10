import { describe, expect, it } from 'vitest';
import {
    decodeFrame,
    decodeSignatureFrame,
    encodeFrame,
    encodeSignatureFrame,
} from '../../src/session/frames';
import { SessionReject } from '../../src/session/reject';
import { FrameType, Role } from '../../src/session/wire';

const HELLO = FrameType.RECONNECT_HELLO;

describe('pre-auth framing', () => {
    it('rejects wrong length', () => {
        const frame = Uint8Array.of(HELLO, 0x00, 0x05, 0x01, 0x02, 0x03);
        expect(() => decodeFrame(frame)).toThrow(SessionReject);
    });

    it('rejects trailing bytes', () => {
        const encoded = encodeFrame(HELLO, Uint8Array.of(0x01, 0x02, 0x03));
        const trailing = new Uint8Array(encoded.length + 1);
        trailing.set(encoded);
        expect(() => decodeFrame(trailing)).toThrow(SessionReject);
    });

    it('rejects frames over 4096 bytes', () => {
        const payload = new Uint8Array(4094);
        const frame = new Uint8Array(3 + payload.length);
        frame[0] = HELLO;
        frame[1] = 0x0f;
        frame[2] = 0xfe;
        frame.set(payload, 3);
        expect(frame.length).toBe(4097);
        expect(() => decodeFrame(frame)).toThrow(SessionReject);
        expect(() => encodeFrame(HELLO, payload)).toThrow(SessionReject);
    });

    it('rejects unknown frame types', () => {
        for (const type of [0x08, 0x10]) {
            expect(() => encodeFrame(type, new Uint8Array(0))).toThrow(SessionReject);
            expect(() => decodeFrame(Uint8Array.of(type, 0x00, 0x00))).toThrow(SessionReject);
        }
    });

    it('rejects pairing frame types', () => {
        for (let type = 0x01; type <= 0x05; type += 1) {
            expect(() => encodeFrame(type, new Uint8Array(0))).toThrow(SessionReject);
            expect(() => decodeFrame(Uint8Array.of(type, 0x00, 0x00))).toThrow(SessionReject);
        }
    });

    it('rejects a non-empty preauth reject payload', () => {
        const empty = encodeFrame(FrameType.PREAUTH_REJECT, new Uint8Array(0));
        const decoded = decodeFrame(empty);
        expect(decoded.type).toBe(FrameType.PREAUTH_REJECT);
        expect(decoded.payload.length).toBe(0);

        expect(() => encodeFrame(FrameType.PREAUTH_REJECT, Uint8Array.of(0x00))).toThrow(
            SessionReject,
        );
        expect(() =>
            decodeFrame(Uint8Array.of(FrameType.PREAUTH_REJECT, 0x00, 0x01, 0x00)),
        ).toThrow(SessionReject);
    });
});

describe('signature frame payloads', () => {
    it('round-trips a 65-byte role and signature payload', () => {
        const signature = Uint8Array.from({ length: 64 }, (_, index) => index);
        const encoded = encodeSignatureFrame({ role: Role.PHONE, signature });
        expect(encoded.length).toBe(65);
        expect(encoded[0]).toBe(Role.PHONE);
        const decoded = decodeSignatureFrame(encoded, Role.PHONE);
        expect(decoded.role).toBe(Role.PHONE);
        expect(decoded.signature).toEqual(signature);
    });

    it('rejects a wrong width, wrong role or unknown role', () => {
        const signature = new Uint8Array(64);
        expect(() =>
            encodeSignatureFrame({ role: Role.PHONE, signature: new Uint8Array(63) }),
        ).toThrow(SessionReject);

        const phoneFrame = encodeSignatureFrame({ role: Role.PHONE, signature });
        expect(() => decodeSignatureFrame(phoneFrame, Role.CLIENT)).toThrow(SessionReject);
        expect(() => decodeSignatureFrame(new Uint8Array(64), Role.PHONE)).toThrow(SessionReject);

        const unknownRole = new Uint8Array(65);
        unknownRole[0] = 0x03;
        expect(() => decodeSignatureFrame(unknownRole, Role.PHONE)).toThrow(SessionReject);

        const trailing = new Uint8Array(66);
        trailing[0] = Role.PHONE;
        expect(() => decodeSignatureFrame(trailing, Role.PHONE)).toThrow(SessionReject);
    });
});
