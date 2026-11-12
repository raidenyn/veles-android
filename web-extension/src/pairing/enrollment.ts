import { equalConstantTime } from '../session/engine';
import { concat, hkdfExpand, hkdfExtract, sha256 } from '../session/schedule';

// OTP2-CRY-007a/007b/007c, OTP2-CRY-020a/020b, OTP2-REC-010/011a/011.
//
// The Kotlin `me.nagaev.veles.pairing.EnrollmentSchedule`/`EnrollmentRecords` mirror
// of this module. Every label is the verbatim ASCII of §5.1/§5.3; the enrollment
// salt length prefix is RFC 9382's u64 little-endian `len(AAD)`, every other
// length prefix here is Veles u16 big-endian.

const SALT_LABEL = 'Veles SPAKE2 enrollment salt v1';
const A_TO_B_AES_LABEL = 'Veles enrollment A-to-B AES key v1';
const A_TO_B_NONCE_LABEL = 'Veles enrollment A-to-B nonce v1';
const A_CONFIRMATION_LABEL = 'Veles enrollment A confirmation v1';
const B_TO_A_AES_LABEL = 'Veles enrollment B-to-A AES key v1';
const B_TO_A_NONCE_LABEL = 'Veles enrollment B-to-A nonce v1';
const B_CONFIRMATION_LABEL = 'Veles enrollment B confirmation v1';
const SESSION_ID_LABEL = 'Veles enrollment session v1';
const RECORD_FINGERPRINT_LABEL = 'Veles enrollment record fingerprint v1';
const PAIR_FINGERPRINT_LABEL = 'Veles identity fingerprint v1';
const TRANSCRIPT_LABEL = 'Veles enrollment transcript v1';

const KE_BYTES = 16;
const HASH_BYTES = 32;
const AES_KEY_BYTES = 32;
const NONCE_PREFIX_BYTES = 4;
const ENROLLMENT_NONCE_BYTES = 12;
const MAX_CONTEXT_BYTES = 4096;

const INSTALLATION_BYTES = 16;
const LABEL_MAX_BYTES = 32;
const PUBKEY_BYTES = 65;
const FINGERPRINT_BYTES = 32;
const NONCE_BYTES = 16;
const BODY_MIN_BYTES = 132;
const BODY_MAX_BYTES = 164;
const POINT_TAG = 0x04;
const LENGTH_OFFSET = 16;
const LABEL_OFFSET = 18;
const U8_MASK = 0xff;
const BITS_PER_BYTE = 8;
const HIGH_BYTE_SHIFT = 8;
const CONFIRM_BYTES = 32;
const DISPLAY_BYTES = 6;
const DISPLAY_GROUP_CHARS = 4;
const HEX_BYTE_CHARS = 2;

const encoder = new TextEncoder();
const utf8Decoder = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });

/** OTP2-REC-010 `device_type` values; only these three exist in protocol version 1. */
export const DeviceType = {
    PHONE: 0x00,
    DESKTOP: 0x01,
    WATCH: 0x02,
} as const;

export type DeviceType = (typeof DeviceType)[keyof typeof DeviceType];

/**
 * The sender's role. It fixes both the OTP2-REC-011a fingerprint `role_byte` and
 * the OTP2-REC-010 `device_type` set: the phone's record is `0x00`, a client
 * record is `0x01` (desktop) or `0x02` (watch).
 */
export const IdentityRole = {
    PHONE: 0x01,
    CLIENT: 0x02,
} as const;

export type IdentityRole = (typeof IdentityRole)[keyof typeof IdentityRole];

const ROLE_DEVICE_TYPES: Record<IdentityRole, readonly DeviceType[]> = {
    [IdentityRole.PHONE]: [DeviceType.PHONE],
    [IdentityRole.CLIENT]: [DeviceType.DESKTOP, DeviceType.WATCH],
};

/** One canonical `enroll_identity` plaintext body (OTP2-REC-010). */
export interface IdentityRecord {
    installationId: Uint8Array;
    label: string;
    deviceType: DeviceType;
    identityPubkey: Uint8Array;
    fingerprint: Uint8Array;
    nonce: Uint8Array;
}

/** OTP2-CRY-020b canonical transcript input: both public records and the SPAKE2 exchange. */
export interface EnrollmentTranscript {
    context: Uint8Array;
    phonePoint: Uint8Array;
    clientPoint: Uint8Array;
    phoneConfirm: Uint8Array;
    clientConfirm: Uint8Array;
    phoneIdentityBody: Uint8Array;
    clientIdentityBody: Uint8Array;
}

/** The six §5.1 enrollment outputs, held in memory for the enrollment connection only. */
export interface EnrollmentKeys {
    aToBAesKey: Uint8Array;
    aToBNoncePrefix: Uint8Array;
    phoneActivationKey: Uint8Array;
    bToAAesKey: Uint8Array;
    bToANoncePrefix: Uint8Array;
    clientActivationKey: Uint8Array;
}

const wipedHolders = new WeakSet<EnrollmentKeys>();

/** Generic enrollment failure. It carries no message and never a record or key value. */
export class EnrollmentReject extends Error {
    constructor() {
        super();
        this.name = 'EnrollmentReject';
    }
}

function reject(): never {
    throw new EnrollmentReject();
}

function label(value: string): Uint8Array {
    return encoder.encode(value);
}

function requireSized(value: Uint8Array, size: number): void {
    if (value.length !== size) reject();
}

function requireContext(context: Uint8Array): void {
    if (context.length > MAX_CONTEXT_BYTES) reject();
}

function requirePoint(point: Uint8Array): void {
    requireSized(point, PUBKEY_BYTES);
    if (point[0] !== POINT_TAG) reject();
}

function requireSafeText(value: string): void {
    for (const character of value) {
        const code = character.codePointAt(0) ?? 0;
        if (code < 0x20 || code === 0x7f) reject();
    }
}

function encodeLabel(value: string): Uint8Array {
    const bytes = encoder.encode(value);
    if (bytes.length > LABEL_MAX_BYTES) reject();
    if (utf8Decoder.decode(bytes) !== value) reject();
    requireSafeText(value);
    return bytes;
}

function decodeLabel(body: Uint8Array, offset: number, length: number): string {
    let value: string;
    try {
        value = utf8Decoder.decode(body.subarray(offset, offset + length));
    } catch {
        return reject();
    }
    requireSafeText(value);
    return value;
}

function u16(value: number): Uint8Array {
    return Uint8Array.of((value >>> HIGH_BYTE_SHIFT) & U8_MASK, value & U8_MASK);
}

function readU16(value: Uint8Array, offset: number): number {
    return ((value[offset] & U8_MASK) << BITS_PER_BYTE) | (value[offset + 1] & U8_MASK);
}

function lengthPrefixed(value: Uint8Array): Uint8Array {
    return concat(u16(value.length), value);
}

function writeU64(value: Uint8Array, offset: number, sequence: bigint): void {
    if (sequence < 0n || sequence > 0xffffffffffffffffn) reject();
    let remaining = sequence;
    for (let index = offset + 7; index >= offset; index -= 1) {
        value[index] = Number(remaining & 0xffn);
        remaining >>= 8n;
    }
}

function roleAccepts(role: IdentityRole, deviceType: DeviceType): boolean {
    return ROLE_DEVICE_TYPES[role].includes(deviceType);
}

/** OTP2-CRY-007a: `HKDF-Extract(SHA-256(label || len64le(ctx) || ctx), Ke)`. */
async function enrollmentSalt(context: Uint8Array): Promise<Uint8Array> {
    const length = new Uint8Array(8);
    let remaining = BigInt(context.length);
    for (let index = 0; index < 8; index += 1) {
        length[index] = Number(remaining & 0xffn);
        remaining >>= 8n;
    }
    return sha256(concat(label(SALT_LABEL), length, context));
}

/**
 * Derives the six §5.1 enrollment keys from the core's 16-byte `Ke` and the
 * canonical public context. The PRK is zeroed before returning; the caller owns
 * the raw holder and must `wipeEnrollmentKeys` it (or use `withEnrollmentKeys`).
 */
export async function deriveEnrollmentKeys(
    ke: Uint8Array,
    context: Uint8Array,
): Promise<EnrollmentKeys> {
    requireSized(ke, KE_BYTES);
    requireContext(context);
    const prk = await hkdfExtract(await enrollmentSalt(context), ke);
    const produced: Uint8Array[] = [];
    const produce = async (labelText: string, size: number): Promise<Uint8Array> => {
        const value = await hkdfExpand(prk, label(labelText), size);
        produced.push(value);
        return value;
    };
    try {
        return {
            aToBAesKey: await produce(A_TO_B_AES_LABEL, AES_KEY_BYTES),
            aToBNoncePrefix: await produce(A_TO_B_NONCE_LABEL, NONCE_PREFIX_BYTES),
            phoneActivationKey: await produce(A_CONFIRMATION_LABEL, AES_KEY_BYTES),
            bToAAesKey: await produce(B_TO_A_AES_LABEL, AES_KEY_BYTES),
            bToANoncePrefix: await produce(B_TO_A_NONCE_LABEL, NONCE_PREFIX_BYTES),
            clientActivationKey: await produce(B_CONFIRMATION_LABEL, AES_KEY_BYTES),
        };
    } catch (failure) {
        for (const field of produced) field.fill(0);
        throw failure;
    } finally {
        prk.fill(0);
    }
}

/** True once `wipeEnrollmentKeys` has zeroed this holder. */
export function isEnrollmentKeysWiped(keys: EnrollmentKeys): boolean {
    return wipedHolders.has(keys);
}

/** Zeros every derived field. The holder is unusable afterwards. */
export function wipeEnrollmentKeys(keys: EnrollmentKeys): void {
    for (const field of Object.values(keys)) field.fill(0);
    wipedHolders.add(keys);
}

/**
 * Runs `block` and wipes the holder on both the success and the failure path. A wiped
 * holder is rejected before the block runs, so an all-zero key set can never be used.
 */
export async function withEnrollmentKeys<T>(
    keys: EnrollmentKeys,
    block: (keys: EnrollmentKeys) => T | Promise<T>,
): Promise<T> {
    if (wipedHolders.has(keys)) reject();
    try {
        return await block(keys);
    } finally {
        wipeEnrollmentKeys(keys);
    }
}

/** OTP2-CRY-020a: `SHA-256("Veles enrollment session v1" || ctx)` first 8 bytes. */
export async function enrollmentSessionPrefix(context: Uint8Array): Promise<Uint8Array> {
    requireContext(context);
    return (await sha256(concat(label(SESSION_ID_LABEL), context))).slice(0, 8);
}

/** OTP2-CRY-007c: the directional 4-byte prefix followed by u64be(sequence). */
export function enrollmentNonce(prefix: Uint8Array, sequence: bigint): Uint8Array {
    requireSized(prefix, NONCE_PREFIX_BYTES);
    const nonce = new Uint8Array(ENROLLMENT_NONCE_BYTES);
    nonce.set(prefix, 0);
    writeU64(nonce, NONCE_PREFIX_BYTES, sequence);
    return nonce;
}

/** Builds a record and computes its OTP2-REC-011a own-record fingerprint. */
export async function createIdentityRecord(
    fields: Omit<IdentityRecord, 'fingerprint'>,
    role: IdentityRole,
): Promise<IdentityRecord> {
    if (fields.installationId.length !== INSTALLATION_BYTES) reject();
    encodeLabel(fields.label);
    requirePoint(fields.identityPubkey);
    requireSized(fields.nonce, NONCE_BYTES);
    if (!roleAccepts(role, fields.deviceType)) reject();
    const record: IdentityRecord = { ...fields, fingerprint: new Uint8Array(FINGERPRINT_BYTES) };
    return { ...record, fingerprint: await recordFingerprint(record, role) };
}

/** Encodes the canonical body. The role-bound device type is enforced here. */
export function encodeIdentityRecord(record: IdentityRecord, role: IdentityRole): Uint8Array {
    if (!roleAccepts(role, record.deviceType)) reject();
    const labelBytes = encodeLabel(record.label);
    requirePoint(record.identityPubkey);
    requireSized(record.fingerprint, FINGERPRINT_BYTES);
    requireSized(record.nonce, NONCE_BYTES);
    if (record.installationId.length !== INSTALLATION_BYTES) reject();

    const body = concat(
        record.installationId,
        u16(labelBytes.length),
        labelBytes,
        Uint8Array.of(record.deviceType),
        record.identityPubkey,
        record.fingerprint,
        record.nonce,
    );
    if (body.length < BODY_MIN_BYTES || body.length > BODY_MAX_BYTES) reject();
    return body;
}

/** Decodes one body, enforcing bounds, role-bound device type, point format and safe text. */
export function decodeIdentityRecord(body: Uint8Array, role: IdentityRole): IdentityRecord {
    if (body.length < BODY_MIN_BYTES || body.length > BODY_MAX_BYTES) reject();
    const labelLength = readU16(body, LENGTH_OFFSET);
    if (labelLength > LABEL_MAX_BYTES) reject();
    if (body.length !== BODY_MIN_BYTES + labelLength) reject();
    const labelValue = decodeLabel(body, LABEL_OFFSET, labelLength);
    const deviceOffset = LABEL_OFFSET + labelLength;
    const deviceType = body[deviceOffset];
    if (
        deviceType !== DeviceType.PHONE &&
        deviceType !== DeviceType.DESKTOP &&
        deviceType !== DeviceType.WATCH
    ) {
        reject();
    }
    if (!roleAccepts(role, deviceType)) reject();
    const pubkeyOffset = deviceOffset + 1;
    const fingerprintOffset = pubkeyOffset + PUBKEY_BYTES;
    const nonceOffset = fingerprintOffset + FINGERPRINT_BYTES;
    if (nonceOffset + NONCE_BYTES !== body.length) reject();
    const identityPubkey = body.slice(pubkeyOffset, fingerprintOffset);
    requirePoint(identityPubkey);
    return {
        installationId: body.slice(0, INSTALLATION_BYTES),
        label: labelValue,
        deviceType,
        identityPubkey,
        fingerprint: body.slice(fingerprintOffset, nonceOffset),
        nonce: body.slice(nonceOffset, nonceOffset + NONCE_BYTES),
    };
}

/**
 * Decodes and verifies the own-record fingerprint before returning. This proves the
 * OTP2-REC-011a binding only; it is NOT a full authentication: the point is still only
 * format-checked. Callers that persist must use `decodeIdentityRecordForStorage` with a
 * point validator.
 */
export async function decodeFingerprintVerifiedIdentityRecord(
    body: Uint8Array,
    role: IdentityRole,
): Promise<IdentityRecord> {
    const record = decodeIdentityRecord(body, role);
    const expected = await recordFingerprint(record, role);
    // OTP2-REC-011a: constant-time comparison, never a direct equality.
    if (!equalConstantTime(expected, record.fingerprint)) reject();
    return record;
}

/**
 * Decode, verify the own-record fingerprint, run `validatePoint` on the identity public key,
 * and only then invoke `store`. Task 8 passes `requireValidPoint` here; a bad fingerprint or an
 * invalid point invokes neither the later validation nor the store.
 */
export async function decodeIdentityRecordForStorage(
    body: Uint8Array,
    role: IdentityRole,
    validatePoint: (point: Uint8Array) => void | Promise<void>,
    store?: (record: IdentityRecord) => void,
): Promise<IdentityRecord> {
    const record = await decodeFingerprintVerifiedIdentityRecord(body, role);
    await validatePoint(record.identityPubkey);
    store?.(record);
    return record;
}

/** OTP2-REC-011a: `SHA-256(label || role_byte || installation_id || identity_pubkey)`. */
export async function recordFingerprint(
    record: IdentityRecord,
    role: IdentityRole,
): Promise<Uint8Array> {
    if (!roleAccepts(role, record.deviceType)) reject();
    if (record.installationId.length !== INSTALLATION_BYTES) reject();
    requirePoint(record.identityPubkey);
    return sha256(
        concat(
            label(RECORD_FINGERPRINT_LABEL),
            Uint8Array.of(role),
            record.installationId,
            record.identityPubkey,
        ),
    );
}

/** OTP2-REC-011: `SHA-256(label || phone_identity_pubkey || client_identity_pubkey)`. */
export async function pairFingerprint(
    phoneIdentityPubkey: Uint8Array,
    clientIdentityPubkey: Uint8Array,
): Promise<Uint8Array> {
    requirePoint(phoneIdentityPubkey);
    requirePoint(clientIdentityPubkey);
    return sha256(concat(label(PAIR_FINGERPRINT_LABEL), phoneIdentityPubkey, clientIdentityPubkey));
}

/** First 6 bytes of `fp_pair` as `XXXX XXXX XXXX` (12 uppercase hex characters). */
export function fingerprintDisplay(pair: Uint8Array): string {
    if (pair.length !== HASH_BYTES) reject();
    const display = Array.from(pair.subarray(0, DISPLAY_BYTES), (byte) =>
        byte.toString(16).padStart(HEX_BYTE_CHARS, '0').toUpperCase(),
    ).join('');
    const groups: string[] = [];
    for (let index = 0; index < display.length; index += DISPLAY_GROUP_CHARS) {
        groups.push(display.slice(index, index + DISPLAY_GROUP_CHARS));
    }
    return groups.join(' ');
}

/**
 * OTP2-CRY-020b canonical `ET`. It binds the public context, both public SPAKE2 points and
 * confirmations, and both canonical identity bodies; it contains no secret-derived value.
 */
export async function transcript(input: EnrollmentTranscript): Promise<Uint8Array> {
    if (input.context.length > MAX_CONTEXT_BYTES) reject();
    requirePoint(input.phonePoint);
    requirePoint(input.clientPoint);
    requireSized(input.phoneConfirm, CONFIRM_BYTES);
    requireSized(input.clientConfirm, CONFIRM_BYTES);
    requireIdentityBodySize(input.phoneIdentityBody);
    requireIdentityBodySize(input.clientIdentityBody);
    return sha256(
        concat(
            label(TRANSCRIPT_LABEL),
            lengthPrefixed(input.context),
            lengthPrefixed(input.phonePoint),
            lengthPrefixed(input.clientPoint),
            lengthPrefixed(input.phoneConfirm),
            lengthPrefixed(input.clientConfirm),
            lengthPrefixed(input.phoneIdentityBody),
            lengthPrefixed(input.clientIdentityBody),
        ),
    );
}

function requireIdentityBodySize(body: Uint8Array): void {
    if (body.length < BODY_MIN_BYTES || body.length > BODY_MAX_BYTES) reject();
}
