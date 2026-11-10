import { describe, expect, it } from 'vitest';
import { requireValidPoint } from '../../src/session/p256';
import {
    confirmation,
    confirmInput,
    deriveKeyMaterial,
    derivePrk,
    deriveSalt,
    importKeys,
    rbase as reconnectRbase,
} from '../../src/session/schedule';
import { SessionReject } from '../../src/session/reject';
import { sealRecord } from '../../src/session/record';
import { Direction, RecordType } from '../../src/session/wire';
import { RECONNECT, RECORD_CONFIRM, bytes, hex, input, outputs, str } from './fixtures';

const fixtureInput = input(RECONNECT);
const fixtureOutputs = outputs(RECONNECT);

const SHARED_SECRET_BYTES = 32;

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

describe('reconnect key schedule', () => {
    it('matches the fixture key schedule', async () => {
        const phoneNonce = bytes(fixtureInput, 'phone_nonce');
        const clientNonce = bytes(fixtureInput, 'client_nonce');
        const ss = await sharedSecret();

        expect(ss).toEqual(bytes(fixtureOutputs, 'ss'));
        expect(await deriveSalt(phoneNonce, clientNonce)).toEqual(
            bytes(fixtureOutputs, 'reconnect_salt'),
        );
        expect(await derivePrk(ss, phoneNonce, clientNonce)).toEqual(
            bytes(fixtureOutputs, 'reconnect_prk'),
        );

        const keys = await deriveKeyMaterial(ss, phoneNonce, clientNonce);
        expect(keys.c2pKey).toEqual(bytes(fixtureOutputs, 'k_ba_aes'));
        expect(keys.c2pPrefix).toEqual(bytes(fixtureOutputs, 'k_ba_nonce_prefix'));
        expect(keys.clientConfirmKey).toEqual(bytes(fixtureOutputs, 'k_ba_confirmation'));
        expect(keys.p2cKey).toEqual(bytes(fixtureOutputs, 'k_ab_aes'));
        expect(keys.p2cPrefix).toEqual(bytes(fixtureOutputs, 'k_ab_nonce_prefix'));
        expect(keys.phoneConfirmKey).toEqual(bytes(fixtureOutputs, 'k_ab_confirmation'));
        expect(keys.sessionId).toEqual(bytes(fixtureOutputs, 'session_id'));

        const transcript = reconnectRbase(
            bytes(fixtureOutputs, 'phone_hello_payload'),
            bytes(fixtureOutputs, 'client_hello_payload'),
        );
        expect(transcript).toEqual(bytes(fixtureOutputs, 'rbase'));

        const inputBytes = await confirmInput(
            transcript,
            bytes(fixtureOutputs, 'phone_sig'),
            bytes(fixtureOutputs, 'client_sig'),
        );
        expect(inputBytes).toEqual(bytes(fixtureOutputs, 'reconnect_confirm_input'));
        expect(await confirmation(keys.phoneConfirmKey, inputBytes)).toEqual(
            bytes(fixtureOutputs, 'conf_a'),
        );
        expect(await confirmation(keys.clientConfirmKey, inputBytes)).toEqual(
            bytes(fixtureOutputs, 'conf_b'),
        );
    });

    it('imports non-extractable AES keys usable for record sealing', async () => {
        const phoneNonce = bytes(fixtureInput, 'phone_nonce');
        const clientNonce = bytes(fixtureInput, 'client_nonce');
        const material = await deriveKeyMaterial(await sharedSecret(), phoneNonce, clientNonce);
        const keys = await importKeys(material);

        expect(keys.c2pKey.extractable).toBe(false);
        expect(keys.p2cKey.extractable).toBe(false);
        expect(keys.c2pKey.algorithm.name).toBe('AES-GCM');
        expect(keys.p2cKey.algorithm.name).toBe('AES-GCM');
        expect(keys.sessionId.subarray(0, 8)).toEqual(bytes(fixtureOutputs, 'session_id_prefix'));

        const recordInput = input(RECORD_CONFIRM);
        const sealed = await sealRecord(
            keys.c2pKey,
            keys.c2pPrefix,
            keys.sessionId.subarray(0, 8),
            Direction.CLIENT_TO_PHONE,
            RecordType.RECONNECT_CONFIRM,
            0n,
            bytes(recordInput, 'body'),
        );
        expect(sealed).toEqual(bytes(outputs(RECORD_CONFIRM), 'record'));
    });

    it('rejects a zero or malformed shared secret', async () => {
        const phoneNonce = bytes(fixtureInput, 'phone_nonce');
        const clientNonce = bytes(fixtureInput, 'client_nonce');
        await expect(
            deriveKeyMaterial(new Uint8Array(SHARED_SECRET_BYTES), phoneNonce, clientNonce),
        ).rejects.toBeInstanceOf(SessionReject);
        await expect(
            deriveKeyMaterial(new Uint8Array(SHARED_SECRET_BYTES - 1), phoneNonce, clientNonce),
        ).rejects.toBeInstanceOf(SessionReject);
    });

    it('rejects malformed nonces', async () => {
        const ss = await sharedSecret();
        await expect(
            deriveKeyMaterial(ss, new Uint8Array(15), bytes(fixtureInput, 'client_nonce')),
        ).rejects.toBeInstanceOf(SessionReject);
    });
});
