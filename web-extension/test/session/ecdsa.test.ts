import { describe, expect, it } from 'vitest';
import { normalizeLowS, signRbase, verifyP1363 } from '../../src/session/ecdsa';
import { SessionReject } from '../../src/session/reject';
import { RECONNECT, bytes, deviation, outputs } from './fixtures';

const fixtureOutputs = outputs(RECONNECT);
const rbase = bytes(fixtureOutputs, 'rbase');
const phonePublic = bytes(fixtureOutputs, 'phone_identity_pub');
const clientPublic = bytes(fixtureOutputs, 'client_identity_pub');
const phoneSignature = bytes(fixtureOutputs, 'phone_sig');

const ORDER = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551n;
const HALF_ORDER = ORDER >> 1n;

function bigintOf(bytesValue: Uint8Array): bigint {
    let value = 0n;
    for (const byte of bytesValue) value = (value << 8n) | BigInt(byte);
    return value;
}

function sha256(value: Uint8Array): Promise<Uint8Array> {
    return crypto.subtle.digest('SHA-256', value).then((digest) => new Uint8Array(digest));
}

describe('strict P1363 ECDSA', () => {
    it('verifies the fixture signatures', async () => {
        expect(phoneSignature.length).toBe(64);
        expect(bytes(fixtureOutputs, 'client_sig').length).toBe(64);
        expect(await verifyP1363(phonePublic, rbase, phoneSignature)).toBe(true);
        expect(await verifyP1363(clientPublic, rbase, bytes(fixtureOutputs, 'client_sig'))).toBe(
            true,
        );
    });

    it('rejects the negative signature fixtures', async () => {
        for (const id of ['veles-neg-sig-high-s', 'veles-neg-sig-der', 'veles-neg-sig-truncated']) {
            const signature = bytes(deviation(id), 'value');
            expect(await verifyP1363(phonePublic, rbase, signature), id).toBe(false);
        }
    });

    it('rejects a double-hashed signature', async () => {
        const pair = await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, true, [
            'sign',
            'verify',
        ]);
        const publicPoint = new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey));
        const doubleHashed = await sha256(rbase);
        const signature = new Uint8Array(
            await crypto.subtle.sign(
                { name: 'ECDSA', hash: 'SHA-256' },
                pair.privateKey,
                doubleHashed,
            ),
        );
        const normalized = normalizeLowS(signature);

        // The signature is a valid single-hash ECDSA signature over the
        // double-hashed message...
        expect(await verifyP1363(publicPoint, doubleHashed, normalized)).toBe(true);
        // ...so verification over rbase itself must reject the extra hash.
        expect(await verifyP1363(publicPoint, rbase, normalized)).toBe(false);
    });

    it('rejects a signature from the wrong key', async () => {
        expect(await verifyP1363(clientPublic, rbase, phoneSignature)).toBe(false);
        expect(await verifyP1363(phonePublic, rbase, bytes(fixtureOutputs, 'client_sig'))).toBe(
            false,
        );
    });

    it('normalizes high-S signatures to low-S', () => {
        const highS = bytes(deviation('veles-neg-sig-high-s'), 'value');
        expect(highS.length).toBe(64);
        expect(normalizeLowS(highS)).toEqual(phoneSignature);
        expect(normalizeLowS(phoneSignature)).toEqual(phoneSignature);

        expect(() => normalizeLowS(new Uint8Array(63))).toThrow(SessionReject);
        const overOrder = new Uint8Array(64);
        overOrder.fill(0xff, 32);
        expect(() => normalizeLowS(overOrder)).toThrow(SessionReject);
    });

    it('signs rbase with a low-S P1363 signature', async () => {
        const pair = await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, true, [
            'sign',
            'verify',
        ]);
        const publicPoint = new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey));
        const signature = await signRbase(pair.privateKey, rbase);
        expect(signature.length).toBe(64);
        expect(bigintOf(signature.subarray(32)) <= HALF_ORDER).toBe(true);
        expect(normalizeLowS(signature)).toEqual(signature);
        expect(await verifyP1363(publicPoint, rbase, signature)).toBe(true);
    });
});
