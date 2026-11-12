import { describe, expect, it } from 'vitest';
import { requireValidPoint } from '../../src/session/p256';
import { deriveKeyMaterial, importKeys } from '../../src/session/schedule';
import { RECONNECT, bytes, hex, input, outputs, str } from './fixtures';
import { PEER_STATE_ACTIVE, PhoneHarness } from './client-harness';

// OTP2-CRY-019f recovery activation keys: the `crypto.json` vectors, the
// no-`ET` case and the mandatory erasure once the session class is `full`.

const fixtureInput = input(RECONNECT);
const fixtureOutputs = outputs(RECONNECT);

function base64url(value: Uint8Array): string {
    let binary = '';
    for (const byte of value) binary += String.fromCharCode(byte);
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

async function ephemeralPrivateKey(scalar: string, publicPoint: string): Promise<CryptoKey> {
    const point = hex(publicPoint);
    const jwk: JsonWebKey = {
        kty: 'EC',
        crv: 'P-256',
        d: base64url(hex(scalar)),
        x: base64url(point.subarray(1, 33)),
        y: base64url(point.subarray(33, 65)),
    };
    return crypto.subtle.importKey('jwk', jwk, { name: 'ECDH', namedCurve: 'P-256' }, false, [
        'deriveBits',
    ]);
}

async function ecdhX(privateKey: CryptoKey, peerPoint: Uint8Array): Promise<Uint8Array> {
    const peer = await requireValidPoint(peerPoint);
    return new Uint8Array(
        await crypto.subtle.deriveBits({ name: 'ECDH', public: peer }, privateKey, 256),
    );
}

async function sharedSecret(): Promise<Uint8Array> {
    const phonePrivate = await ephemeralPrivateKey(
        str(fixtureInput, 'phone_ephemeral_priv'),
        str(fixtureOutputs, 'phone_ephemeral_pub'),
    );
    const clientPrivate = await ephemeralPrivateKey(
        str(fixtureInput, 'client_ephemeral_priv'),
        str(fixtureOutputs, 'client_ephemeral_pub'),
    );
    const fromPhone = await ecdhX(phonePrivate, bytes(fixtureOutputs, 'client_ephemeral_pub'));
    const fromClient = await ecdhX(clientPrivate, bytes(fixtureOutputs, 'phone_ephemeral_pub'));
    expect(fromPhone).toEqual(fromClient);
    return fromPhone;
}

describe('recovery activation keys', () => {
    it('matches the recovery activation key vectors', async () => {
        const keys = await deriveKeyMaterial(
            await sharedSecret(),
            bytes(fixtureInput, 'phone_nonce'),
            bytes(fixtureInput, 'client_nonce'),
            bytes(fixtureInput, 'et'),
        );

        expect(keys.recovery).not.toBeNull();
        expect(keys.recovery?.kRecClient).toEqual(bytes(fixtureOutputs, 'k_rec_client'));
        expect(keys.recovery?.kRecPhone).toEqual(bytes(fixtureOutputs, 'k_rec_phone'));

        // The imported session keys keep the same recovery holder.
        const imported = await importKeys(keys);
        expect(imported.recovery?.kRecClient).toEqual(bytes(fixtureOutputs, 'k_rec_client'));
        expect(imported.recovery?.kRecPhone).toEqual(bytes(fixtureOutputs, 'k_rec_phone'));
    });

    it('derives no recovery keys without ET', async () => {
        const ss = await sharedSecret();
        const phoneNonce = bytes(fixtureInput, 'phone_nonce');
        const clientNonce = bytes(fixtureInput, 'client_nonce');

        const explicitNull = await deriveKeyMaterial(ss, phoneNonce, clientNonce, null);
        expect(explicitNull.recovery).toBeNull();

        const defaulted = await deriveKeyMaterial(ss, phoneNonce, clientNonce);
        expect(defaulted.recovery).toBeNull();
        expect((await importKeys(defaulted)).recovery).toBeNull();
    });

    it('zeroes the recovery keys when the class is full', async () => {
        const harness = await PhoneHarness.create({
            state: 'active',
            et: bytes(fixtureInput, 'et'),
        });
        const drive = await harness.drive();

        // The engine derived both keys from the looked-up record's ET.
        const recovery = harness.engine._recoveryKeys;
        expect(recovery).not.toBeNull();
        expect(recovery?.kRecClient.some((byte) => byte !== 0)).toBe(true);
        expect(recovery?.kRecPhone.some((byte) => byte !== 0)).toBe(true);

        await harness.engine.onSendComplete(drive.token, true);
        const authenticated = (
            await harness.engine.onInbound(await harness.confirmRecord(PEER_STATE_ACTIVE))
        )[0];
        expect(authenticated).toMatchObject({ kind: 'authenticated', sessionClass: 'FULL' });

        // OTP2-CRY-019f: the holder is zeroed once the class is full, and the session stays open.
        expect(harness.engine.isOpen).toBe(true);
        expect(recovery?.kRecClient).toEqual(new Uint8Array(32));
        expect(recovery?.kRecPhone).toEqual(new Uint8Array(32));
    });
});
