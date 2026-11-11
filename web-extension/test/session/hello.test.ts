import { describe, expect, it } from 'vitest';
import { decodeHello, encodeHello } from '../../src/session/hello';
import { requireValidPoint } from '../../src/session/p256';
import { SessionReject } from '../../src/session/reject';
import { Role } from '../../src/session/wire';
import { RECONNECT, bytes, caps, deviation, ids, input, outputs } from './fixtures';

const fixtureInput = input(RECONNECT);
const fixtureOutputs = outputs(RECONNECT);
const phonePayload = bytes(fixtureOutputs, 'phone_hello_payload');
const clientPayload = bytes(fixtureOutputs, 'client_hello_payload');

const HELLO_FIXED = 102;
const HELLO_POINT_OFFSET = 16;
const HELLO_POINT_END = HELLO_POINT_OFFSET + 65;
const HELLO_NONCE_OFFSET = HELLO_POINT_END;
const HELLO_ROLE_OFFSET = HELLO_NONCE_OFFSET + 16;
const HELLO_VERSION_OFFSET = HELLO_ROLE_OFFSET + 1;
const HELLO_CAPS_LEN_OFFSET = HELLO_VERSION_OFFSET + 2;

interface PhoneHelloOptions {
    caps?: Uint8Array;
    version?: number;
}

function phoneHello(options: PhoneHelloOptions = {}): Uint8Array {
    const capsBytes = options.caps ?? caps(fixtureInput, 'phone_caps');
    const version = options.version ?? 1;
    const installationId = bytes(fixtureInput, 'phone_installation_id');
    const point = bytes(fixtureOutputs, 'phone_ephemeral_pub');
    const nonce = bytes(fixtureInput, 'phone_nonce');
    const payload = new Uint8Array(HELLO_FIXED + capsBytes.length);
    payload.set(installationId, 0);
    payload.set(point, HELLO_POINT_OFFSET);
    payload.set(nonce, HELLO_NONCE_OFFSET);
    payload[HELLO_ROLE_OFFSET] = Role.PHONE;
    payload[HELLO_VERSION_OFFSET] = version >>> 8;
    payload[HELLO_VERSION_OFFSET + 1] = version;
    payload[HELLO_CAPS_LEN_OFFSET] = capsBytes.length >>> 8;
    payload[HELLO_CAPS_LEN_OFFSET + 1] = capsBytes.length;
    payload.set(capsBytes, HELLO_FIXED);
    return payload;
}

describe('reconnect hello payloads', () => {
    it('round-trips the fixture hello payloads', () => {
        const phone = decodeHello(phonePayload, Role.PHONE);
        expect(encodeHello(phone)).toEqual(phonePayload);
        expect(phone.installationId).toEqual(bytes(fixtureInput, 'phone_installation_id'));
        expect(phone.ephemeralPublicKey).toEqual(bytes(fixtureOutputs, 'phone_ephemeral_pub'));
        expect(phone.nonce).toEqual(bytes(fixtureInput, 'phone_nonce'));
        expect(phone.role).toBe(Role.PHONE);
        expect(phone.caps).toEqual(caps(fixtureInput, 'phone_caps'));

        const client = decodeHello(clientPayload, Role.CLIENT);
        expect(encodeHello(client)).toEqual(clientPayload);
        expect(client.installationId).toEqual(bytes(fixtureInput, 'client_installation_id'));
        expect(client.ephemeralPublicKey).toEqual(bytes(fixtureOutputs, 'client_ephemeral_pub'));
        expect(client.nonce).toEqual(bytes(fixtureInput, 'client_nonce'));
        expect(client.role).toBe(Role.CLIENT);
        expect(client.caps).toEqual(caps(fixtureInput, 'client_caps'));
    });

    it('rejects the negative point fixtures', async () => {
        const negatives = ids('veles-neg-point-');
        expect(negatives.length).toBe(4);
        for (const id of negatives) {
            const malformed = bytes(deviation(id), 'value');
            const spliced = new Uint8Array(
                phonePayload.length - (HELLO_POINT_END - HELLO_POINT_OFFSET) + malformed.length,
            );
            spliced.set(phonePayload.subarray(0, HELLO_POINT_OFFSET), 0);
            spliced.set(malformed, HELLO_POINT_OFFSET);
            spliced.set(
                phonePayload.subarray(HELLO_POINT_END),
                HELLO_POINT_OFFSET + malformed.length,
            );

            expect(() => decodeHello(spliced, Role.PHONE), id).toThrow(SessionReject);
            await expect(requireValidPoint(malformed), id).rejects.toBeInstanceOf(SessionReject);
        }
    });

    it('rejects caps that are unsorted, duplicated or missing 0x01', () => {
        for (const malformed of [
            Uint8Array.of(0x02, 0x01),
            Uint8Array.of(0x01, 0x01),
            Uint8Array.of(0x02),
        ]) {
            expect(() => decodeHello(phoneHello({ caps: malformed }), Role.PHONE)).toThrow(
                SessionReject,
            );
        }
    });

    it('rejects an unknown capability', () => {
        const payload = phoneHello({ caps: Uint8Array.of(0x01, 0x02) });
        expect(() => decodeHello(payload, Role.PHONE, Uint8Array.of(0x01))).toThrow(SessionReject);
    });

    it('rejects version 2', () => {
        expect(() => decodeHello(phoneHello({ version: 2 }), Role.PHONE)).toThrow(SessionReject);
    });

    it('rejects hello payloads naming the wrong role', () => {
        expect(() => decodeHello(phonePayload, Role.CLIENT)).toThrow(SessionReject);
        expect(() => decodeHello(clientPayload, Role.PHONE)).toThrow(SessionReject);
    });
});
