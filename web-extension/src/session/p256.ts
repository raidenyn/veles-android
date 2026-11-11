import { SessionReject } from './reject';

// P-256 point validation over Web Crypto (OTP2-CRY-005a, OTP2-REC-020b).
//
// The curve equation is checked here as well as by `importKey`: some platform
// providers accept an uncompressed encoding without validating the point.
const FIELD_PRIME = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffffn;
const CURVE_B = 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604bn;
const CURVE_A = -3n;

const COORDINATE_BYTES = 32;
const POINT_BYTES = 65;
const UNCOMPRESSED_TAG = 0x04;

function reject(): never {
    throw new SessionReject();
}

function toBigInt(value: Uint8Array): bigint {
    let result = 0n;
    for (const byte of value) result = (result << 8n) | BigInt(byte);
    return result;
}

function modulo(value: bigint, modulus: bigint): bigint {
    return ((value % modulus) + modulus) % modulus;
}

/**
 * Validates an uncompressed SEC1 point strictly: exact length and `0x04`
 * prefix, then the all-zero identity shape, the field range and the curve
 * equation. Any failure is a `SessionReject`.
 */
export function requireValidPointBytes(uncompressed: Uint8Array): void {
    if (uncompressed.length !== POINT_BYTES) reject();
    if (uncompressed[0] !== UNCOMPRESSED_TAG) reject();

    const x = toBigInt(uncompressed.subarray(1, 1 + COORDINATE_BYTES));
    const y = toBigInt(uncompressed.subarray(1 + COORDINATE_BYTES, POINT_BYTES));
    if (x === 0n && y === 0n) reject();
    if (x >= FIELD_PRIME || y >= FIELD_PRIME) reject();

    const left = modulo(y * y, FIELD_PRIME);
    const right = modulo(x * x * x + CURVE_A * x + CURVE_B, FIELD_PRIME);
    if (left !== right) reject();
}

/** Validates the point and imports it as an ECDH-usable public key. */
export async function requireValidPoint(uncompressed: Uint8Array): Promise<CryptoKey> {
    requireValidPointBytes(uncompressed);
    try {
        return await crypto.subtle.importKey(
            'raw',
            uncompressed,
            { name: 'ECDH', namedCurve: 'P-256' },
            false,
            [],
        );
    } catch {
        reject();
    }
}
