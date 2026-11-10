import { requireValidPointBytes } from './p256';
import { SessionReject } from './reject';

// Strict P-256 ECDSA over Web Crypto: 64-byte P1363 `r || s`, low-S
// normalization, and low-S-only verification over a single SHA-256 of the
// message (OTP2-REC-020a).

const COORDINATE_BYTES = 32;
const SIGNATURE_BYTES = 64;

const ORDER = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551n;
const HALF_ORDER = ORDER >> 1n;

function reject(): never {
    throw new SessionReject();
}

function toBigInt(value: Uint8Array): bigint {
    let result = 0n;
    for (const byte of value) result = (result << 8n) | BigInt(byte);
    return result;
}

function fixedCoordinate(value: bigint): Uint8Array {
    const encoded = new Uint8Array(COORDINATE_BYTES);
    let remaining = value;
    for (let index = COORDINATE_BYTES - 1; index >= 0; index -= 1) {
        encoded[index] = Number(remaining & 0xffn);
        remaining >>= 8n;
    }
    return encoded;
}

/** Returns the low-S form of a 64-byte P1363 signature. */
export function normalizeLowS(signature: Uint8Array): Uint8Array {
    if (signature.length !== SIGNATURE_BYTES) reject();
    const s = toBigInt(signature.subarray(COORDINATE_BYTES, SIGNATURE_BYTES));
    if (s >= ORDER) reject();
    const normalized = s > HALF_ORDER ? ORDER - s : s;

    const result = new Uint8Array(SIGNATURE_BYTES);
    result.set(signature.subarray(0, COORDINATE_BYTES), 0);
    result.set(fixedCoordinate(normalized), COORDINATE_BYTES);
    return result;
}

async function importVerifyKey(publicKey: Uint8Array): Promise<CryptoKey | null> {
    requireValidPointBytes(publicKey);
    try {
        return await crypto.subtle.importKey(
            'raw',
            publicKey,
            { name: 'ECDSA', namedCurve: 'P-256' },
            false,
            ['verify'],
        );
    } catch {
        return null;
    }
}

/**
 * Verifies a 64-byte low-S signature over `message` with exactly one SHA-256.
 * Malformed widths, DER encodings, high-S signatures and wrong-key
 * signatures all return false. An invalid public point is a `SessionReject`.
 */
export async function verifyP1363(
    publicKey: Uint8Array,
    message: Uint8Array,
    signature: Uint8Array,
): Promise<boolean> {
    if (signature.length !== SIGNATURE_BYTES) return false;

    const r = toBigInt(signature.subarray(0, COORDINATE_BYTES));
    const s = toBigInt(signature.subarray(COORDINATE_BYTES, SIGNATURE_BYTES));
    if (r === 0n || s === 0n) return false;
    if (r >= ORDER || s > HALF_ORDER) return false;

    const key = await importVerifyKey(publicKey);
    if (key === null) return false;
    try {
        return await crypto.subtle.verify(
            { name: 'ECDSA', hash: 'SHA-256' },
            key,
            signature,
            message,
        );
    } catch {
        return false;
    }
}

/** Signs the reconnect transcript and normalizes the P1363 result to low-S. */
export async function signRbase(key: CryptoKey, rbase: Uint8Array): Promise<Uint8Array> {
    try {
        const signature = new Uint8Array(
            await crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, key, rbase),
        );
        return normalizeLowS(signature);
    } catch {
        return reject();
    }
}
