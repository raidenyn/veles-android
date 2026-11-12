import { SessionReject } from './reject';

// OTP2-REC-020b/020c reconnect key material: the transcript, the ECDH HKDF
// schedule and the confirmation HMACs. All ten protocol labels are exact
// ASCII from the plan's Global Constraints.

const TRANSCRIPT_LABEL = 'Veles reconnect transcript v1';
const SALT_LABEL = 'Veles reconnect salt v1';
const C2P_AES_LABEL = 'Veles reconnect B-to-A AES key v1';
const C2P_NONCE_LABEL = 'Veles reconnect B-to-A nonce v1';
const C2P_CONFIRM_LABEL = 'Veles reconnect B-to-A confirmation v1';
const P2C_AES_LABEL = 'Veles reconnect A-to-B AES key v1';
const P2C_NONCE_LABEL = 'Veles reconnect A-to-B nonce v1';
const P2C_CONFIRM_LABEL = 'Veles reconnect A-to-B confirmation v1';
const SESSION_ID_LABEL = 'Veles reconnect session ID v1';
const CONFIRM_INPUT_LABEL = 'Veles reconnect confirmation input v1';

const NONCE_BYTES = 16;
const SHARED_SECRET_BYTES = 32;
const SIGNATURE_BYTES = 64;
const HASH_BYTES = 32;
const AES_KEY_BYTES = 32;
const NONCE_PREFIX_BYTES = 4;
const SESSION_ID_BYTES = 32;

const HKDF_HASH_BYTES = 32;
const HKDF_MAX_BLOCKS = 255;

const encoder = new TextEncoder();

export type ReconnectKeyName =
    | 'c2pKey'
    | 'c2pPrefix'
    | 'clientConfirmKey'
    | 'p2cKey'
    | 'p2cPrefix'
    | 'phoneConfirmKey'
    | 'sessionId';

/** Raw derived key material. Callers zero the AES bytes after `importKeys`. */
export type ReconnectKeyMaterial = Record<ReconnectKeyName, Uint8Array>;

/** The reconnect keys as the session engine keeps them: AES keys are CryptoKeys. */
export interface ReconnectKeys {
    c2pKey: CryptoKey;
    c2pPrefix: Uint8Array;
    clientConfirmKey: Uint8Array;
    p2cKey: CryptoKey;
    p2cPrefix: Uint8Array;
    phoneConfirmKey: Uint8Array;
    sessionId: Uint8Array;
}

function reject(): never {
    throw new SessionReject();
}

function label(value: string): Uint8Array {
    return encoder.encode(value);
}

export function concat(...parts: Uint8Array[]): Uint8Array {
    let length = 0;
    for (const part of parts) length += part.length;
    const joined = new Uint8Array(length);
    let offset = 0;
    for (const part of parts) {
        joined.set(part, offset);
        offset += part.length;
    }
    return joined;
}

function requireSized(value: Uint8Array, size: number): void {
    if (value.length !== size) reject();
}

function requireSharedSecret(ss: Uint8Array): void {
    requireSized(ss, SHARED_SECRET_BYTES);
    if (ss.every((byte) => byte === 0)) reject();
}

export async function sha256(data: Uint8Array): Promise<Uint8Array> {
    try {
        return new Uint8Array(await crypto.subtle.digest('SHA-256', data));
    } catch {
        return reject();
    }
}

export async function hmacSha256(key: Uint8Array, data: Uint8Array): Promise<Uint8Array> {
    try {
        const hmacKey = await crypto.subtle.importKey(
            'raw',
            key,
            { name: 'HMAC', hash: 'SHA-256' },
            false,
            ['sign'],
        );
        return new Uint8Array(await crypto.subtle.sign('HMAC', hmacKey, data));
    } catch {
        return reject();
    }
}

export async function hkdfExtract(salt: Uint8Array, ikm: Uint8Array): Promise<Uint8Array> {
    return hmacSha256(salt.length === 0 ? new Uint8Array(HKDF_HASH_BYTES) : salt, ikm);
}

export async function hkdfExpand(
    prk: Uint8Array,
    info: Uint8Array,
    length: number,
): Promise<Uint8Array> {
    if (prk.length === 0 || length <= 0 || length > HKDF_MAX_BLOCKS * HKDF_HASH_BYTES) reject();
    const output = new Uint8Array(length);
    let produced = 0;
    let previous: Uint8Array = new Uint8Array(0);
    let counter = 1;
    while (produced < length) {
        previous = await hmacSha256(prk, concat(previous, info, Uint8Array.of(counter)));
        const copy = Math.min(previous.length, length - produced);
        output.set(previous.subarray(0, copy), produced);
        produced += copy;
        counter += 1;
    }
    return output;
}

/** `rbase = transcript_label || phone_hello_payload || client_hello_payload`. */
export function rbase(phoneHello: Uint8Array, clientHello: Uint8Array): Uint8Array {
    return concat(label(TRANSCRIPT_LABEL), phoneHello, clientHello);
}

/** `salt = SHA-256(salt_label || phone_nonce || client_nonce)`. */
export async function deriveSalt(
    phoneNonce: Uint8Array,
    clientNonce: Uint8Array,
): Promise<Uint8Array> {
    requireSized(phoneNonce, NONCE_BYTES);
    requireSized(clientNonce, NONCE_BYTES);
    return sha256(concat(label(SALT_LABEL), phoneNonce, clientNonce));
}

/** `prk = HKDF-Extract(salt, ss)`. */
export async function derivePrk(
    ss: Uint8Array,
    phoneNonce: Uint8Array,
    clientNonce: Uint8Array,
): Promise<Uint8Array> {
    requireSharedSecret(ss);
    return hkdfExtract(await deriveSalt(phoneNonce, clientNonce), ss);
}

/**
 * Derives the seven OTP2-REC-020b outputs under their exact labels and returns
 * the raw bytes. The intermediate PRK is zeroed before returning.
 */
export async function deriveKeyMaterial(
    ss: Uint8Array,
    phoneNonce: Uint8Array,
    clientNonce: Uint8Array,
): Promise<ReconnectKeyMaterial> {
    requireSharedSecret(ss);
    const salt = await deriveSalt(phoneNonce, clientNonce);
    const prk = await hkdfExtract(salt, ss);
    try {
        const [c2pKey, c2pPrefix, clientConfirmKey, p2cKey, p2cPrefix, phoneConfirmKey, sessionId] =
            await Promise.all([
                hkdfExpand(prk, label(C2P_AES_LABEL), AES_KEY_BYTES),
                hkdfExpand(prk, label(C2P_NONCE_LABEL), NONCE_PREFIX_BYTES),
                hkdfExpand(prk, label(C2P_CONFIRM_LABEL), AES_KEY_BYTES),
                hkdfExpand(prk, label(P2C_AES_LABEL), AES_KEY_BYTES),
                hkdfExpand(prk, label(P2C_NONCE_LABEL), NONCE_PREFIX_BYTES),
                hkdfExpand(prk, label(P2C_CONFIRM_LABEL), AES_KEY_BYTES),
                hkdfExpand(prk, label(SESSION_ID_LABEL), SESSION_ID_BYTES),
            ]);
        return {
            c2pKey,
            c2pPrefix,
            clientConfirmKey,
            p2cKey,
            p2cPrefix,
            phoneConfirmKey,
            sessionId,
        };
    } finally {
        prk.fill(0);
    }
}

/** Imports the AES keys as non-extractable; the caller zeroes the raw bytes. */
export async function importKeys(material: ReconnectKeyMaterial): Promise<ReconnectKeys> {
    requireSized(material.c2pKey, AES_KEY_BYTES);
    requireSized(material.c2pPrefix, NONCE_PREFIX_BYTES);
    requireSized(material.clientConfirmKey, AES_KEY_BYTES);
    requireSized(material.p2cKey, AES_KEY_BYTES);
    requireSized(material.p2cPrefix, NONCE_PREFIX_BYTES);
    requireSized(material.phoneConfirmKey, AES_KEY_BYTES);
    requireSized(material.sessionId, SESSION_ID_BYTES);

    const importAes = (raw: Uint8Array): Promise<CryptoKey> =>
        crypto.subtle.importKey('raw', raw, { name: 'AES-GCM' }, false, ['encrypt', 'decrypt']);
    let c2pKey: CryptoKey;
    let p2cKey: CryptoKey;
    try {
        [c2pKey, p2cKey] = await Promise.all([
            importAes(material.c2pKey),
            importAes(material.p2cKey),
        ]);
    } catch {
        return reject();
    }
    return {
        c2pKey,
        c2pPrefix: material.c2pPrefix,
        clientConfirmKey: material.clientConfirmKey,
        p2cKey,
        p2cPrefix: material.p2cPrefix,
        phoneConfirmKey: material.phoneConfirmKey,
        sessionId: material.sessionId,
    };
}

/** `confirm_input = SHA-256(confirmation_label || rbase || phone_sig || client_sig)`. */
export async function confirmInput(
    transcript: Uint8Array,
    phoneSig: Uint8Array,
    clientSig: Uint8Array,
): Promise<Uint8Array> {
    requireSized(phoneSig, SIGNATURE_BYTES);
    requireSized(clientSig, SIGNATURE_BYTES);
    return sha256(concat(label(CONFIRM_INPUT_LABEL), transcript, phoneSig, clientSig));
}

/** `conf = HMAC-SHA-256(own_confirmation_key, confirm_input)`. */
export async function confirmation(key: Uint8Array, input: Uint8Array): Promise<Uint8Array> {
    requireSized(key, AES_KEY_BYTES);
    requireSized(input, HASH_BYTES);
    return hmacSha256(key, input);
}
