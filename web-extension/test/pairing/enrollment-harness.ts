import { pairing_command } from '../../rust-wasm/pkg/veles_crypto.js';
import {
    DeviceType,
    IdentityRole,
    createIdentityRecord,
    decodeFingerprintVerifiedIdentityRecord,
    deriveEnrollmentKeys,
    encodeIdentityRecord,
    enrollmentSessionPrefix,
    transcript,
    wipeEnrollmentKeys,
    type EnrollmentKeys,
} from '../../src/pairing/enrollment';
import { PairingFrameType, decodePairingFrame, encodePairingFrame } from '../../src/pairing/frames';
import { Direction, RecordType } from '../../src/session/wire';
import { openRecord, sealRecord } from '../../src/session/record';
import { concat, hmacSha256 } from '../../src/session/schedule';

// Test-only phone half of the OTP-10 enrollment exchange. The client machine
// under test is the production `ClientMachine`; this peer drives the real WASM
// core as SPAKE2 party A and the production record/identity codecs as the
// phone, mirroring the Kotlin `EnrollmentScenarioTest.TracePeer`. Nothing here
// is imported by product code.

export const PACKAGE_BYTES = new URL('../../rust-wasm/pkg/veles_crypto_bg.wasm', import.meta.url);

const ROLE_A = 1;
const OP_START = 1;
const OP_EXCHANGE = 2;
const OP_CONFIRM = 3;
const OP_FINISH = 4;
const OP_CLOSE = 5;
const STATUS_OK = 0;

const U8_MASK = 0xff;
const BITS_PER_BYTE = 8;
const U16_BYTES = 2;
const PAIRING_ID_BYTES = 16;
const NONCE_BYTES = 16;
const POINT_BYTES = 65;
const CONFIRMATION_BYTES = 32;
const KEY_BYTES = 16;
const HANDLE_BYTES = 8;

const CONTEXT_PREFIX = 'Veles SPAKE2 public context v1';
const PHONE_CAPS = Uint8Array.of(0x01, 0x02);
const PHONE_LABEL = 'Phone';

const openPhoneHandles = new Set<string>();

export function openPhoneHandleCount(): number {
    return openPhoneHandles.size;
}

export function hexOf(value: Uint8Array): string {
    return Array.from(value, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

export function flipLast(value: Uint8Array): Uint8Array {
    const copy = value.slice();
    copy[copy.length - 1] ^= 1;
    return copy;
}

export function flipFirst(value: Uint8Array): Uint8Array {
    const copy = value.slice();
    copy[0] ^= 1;
    return copy;
}

function u16(value: Uint8Array, offset: number): number {
    return ((value[offset] & U8_MASK) << BITS_PER_BYTE) | (value[offset + 1] & U8_MASK);
}

function putU16(target: Uint8Array, offset: number, value: number): number {
    target[offset] = (value >>> BITS_PER_BYTE) & U8_MASK;
    target[offset + 1] = value & U8_MASK;
    return offset + U16_BYTES;
}

function handleKey(handle: Uint8Array): string {
    return hexOf(handle);
}

class RawPhoneExecution {
    private closed = false;

    constructor(private readonly handle: Uint8Array) {
        openPhoneHandles.add(handleKey(handle));
    }

    exchange(peerPoint: Uint8Array): Uint8Array {
        return this.call(OP_EXCHANGE, peerPoint, CONFIRMATION_BYTES);
    }

    confirm(peerConfirmation: Uint8Array): void {
        this.call(OP_CONFIRM, peerConfirmation, 0);
    }

    finish(): Uint8Array {
        const shared = this.call(OP_FINISH, new Uint8Array(0), KEY_BYTES);
        this.closed = true;
        openPhoneHandles.delete(handleKey(this.handle));
        return shared;
    }

    close(): void {
        if (this.closed) return;
        this.closed = true;
        openPhoneHandles.delete(handleKey(this.handle));
        try {
            pairing_command(concat(Uint8Array.of(1, OP_CLOSE), this.handle));
        } catch {
            // Cleanup must never mask the test's real outcome.
        }
    }

    private call(operation: number, body: Uint8Array, expected: number): Uint8Array {
        if (this.closed) throw new Error('phone execution is closed');
        const response = pairing_command(concat(Uint8Array.of(1, operation), this.handle, body));
        if (response[0] !== STATUS_OK) throw new Error('phone core refused');
        const output = response.slice(1);
        if (output.length !== expected) {
            throw new Error(`phone core returned ${output.length} bytes for ${operation}`);
        }
        return output;
    }
}

/** §3.3 canonical public context, built independently of the client machine. */
export function contextOf(
    pairingId: Uint8Array,
    phoneNonce: Uint8Array,
    clientNonce: Uint8Array,
    phoneCaps: Uint8Array,
    clientCaps: Uint8Array,
): Uint8Array {
    const ctx = new Uint8Array(
        30 + 6 + PAIRING_ID_BYTES + 2 + NONCE_BYTES * 2 + 4 + phoneCaps.length + clientCaps.length,
    );
    let offset = 0;
    ctx.set(new TextEncoder().encode(CONTEXT_PREFIX), offset);
    offset += CONTEXT_PREFIX.length;
    offset = putU16(ctx, offset, 1);
    offset = putU16(ctx, offset, 1);
    offset = putU16(ctx, offset, 1);
    ctx.set(pairingId, offset);
    offset += PAIRING_ID_BYTES;
    ctx[offset] = 0x01;
    ctx[offset + 1] = 0x02;
    offset += 2;
    ctx.set(phoneNonce, offset);
    offset += NONCE_BYTES;
    ctx.set(clientNonce, offset);
    offset += NONCE_BYTES;
    offset = putU16(ctx, offset, phoneCaps.length);
    ctx.set(phoneCaps, offset);
    offset += phoneCaps.length;
    putU16(ctx, offset, clientCaps.length);
    ctx.set(clientCaps, offset + U16_BYTES);
    return ctx;
}

interface RequestPayload {
    clientNonce: Uint8Array;
    clientCaps: Uint8Array;
}

function parseRequest(payload: Uint8Array): RequestPayload {
    if (payload.length < NONCE_BYTES + 4) throw new Error('malformed pairing request');
    if (u16(payload, NONCE_BYTES) !== 1) throw new Error('unsupported pairing request version');
    const capsLength = u16(payload, NONCE_BYTES + 2);
    if (payload.length !== NONCE_BYTES + 4 + capsLength) {
        throw new Error('malformed pairing request caps');
    }
    return {
        clientNonce: payload.slice(0, NONCE_BYTES),
        clientCaps: payload.slice(NONCE_BYTES + 4),
    };
}

async function importAes(raw: Uint8Array): Promise<CryptoKey> {
    return crypto.subtle.importKey('raw', raw, { name: 'AES-GCM' }, false, ['encrypt', 'decrypt']);
}

export interface PhonePeerOptions {
    code: string;
    pairingId?: Uint8Array;
    phoneNonce?: Uint8Array;
    identity?: CryptoKeyPair;
    installationId?: Uint8Array;
    identityNonce?: Uint8Array;
}

export interface IdentityFrameOptions {
    fingerprintOk?: boolean;
    deviceTypeOk?: boolean;
    aeadOk?: boolean;
}

export interface RecordOptions {
    macOk?: boolean;
    tagOk?: boolean;
    lengthOk?: boolean;
    sessionIdOk?: boolean;
}

/** The scripted phone of one enrollment connection, on the real core. */
export class PhonePeer {
    readonly pairingId: Uint8Array;
    readonly phoneNonce: Uint8Array;
    readonly identityPubkey: Uint8Array;
    readonly installationId: Uint8Array;
    readonly identityNonce: Uint8Array;

    private execution: RawPhoneExecution | null = null;
    private clientNonce: Uint8Array | null = null;
    private clientCaps: Uint8Array | null = null;
    private phonePoint: Uint8Array | null = null;
    private clientPoint: Uint8Array | null = null;
    private phoneConfirm: Uint8Array | null = null;
    private clientConfirm: Uint8Array | null = null;
    private context: Uint8Array | null = null;
    private keys: EnrollmentKeys | null = null;
    private prefix: Uint8Array | null = null;
    private aToBKey: CryptoKey | null = null;
    private bToAKey: CryptoKey | null = null;
    private phoneIdentityBody: Uint8Array | null = null;
    private clientIdentityBody: Uint8Array | null = null;
    private rxSequence = 0n;
    private txSequence = 0n;

    private constructor(
        private readonly code: string,
        identityPubkey: Uint8Array,
        installationId: Uint8Array,
        pairingId: Uint8Array,
        phoneNonce: Uint8Array,
        identityNonce: Uint8Array,
    ) {
        this.identityPubkey = identityPubkey;
        this.installationId = installationId;
        this.pairingId = pairingId;
        this.phoneNonce = phoneNonce;
        this.identityNonce = identityNonce;
    }

    static async create(options: PhonePeerOptions): Promise<PhonePeer> {
        const identity =
            options.identity ??
            ((await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, true, [
                'sign',
                'verify',
            ])) as CryptoKeyPair);
        const identityPubkey = new Uint8Array(
            await crypto.subtle.exportKey('raw', identity.publicKey),
        );
        return new PhonePeer(
            options.code,
            identityPubkey,
            options.installationId ?? crypto.getRandomValues(new Uint8Array(NONCE_BYTES)),
            options.pairingId ?? crypto.getRandomValues(new Uint8Array(PAIRING_ID_BYTES)),
            options.phoneNonce ?? crypto.getRandomValues(new Uint8Array(NONCE_BYTES)),
            options.identityNonce ?? crypto.getRandomValues(new Uint8Array(NONCE_BYTES)),
        );
    }

    /** Answers a `pairing_request` with a `pairing_offer`. */
    offerFor(requestFrame: Uint8Array, validPoint = true): Uint8Array {
        const frame = decodePairingFrame(requestFrame);
        if (frame.type !== PairingFrameType.REQUEST) throw new Error('expected pairing_request');
        const request = parseRequest(frame.payload);
        this.clientNonce = request.clientNonce;
        this.clientCaps = request.clientCaps;
        this.context = contextOf(
            this.pairingId,
            this.phoneNonce,
            request.clientNonce,
            PHONE_CAPS,
            request.clientCaps,
        );
        if (validPoint) {
            const started = pairing_command(
                concat(
                    Uint8Array.of(1, OP_START, ROLE_A),
                    new TextEncoder().encode(this.code),
                    checkNotNull(this.context),
                ),
            );
            if (started[0] !== STATUS_OK) throw new Error('phone core refused start');
            const handle = started.slice(1, 1 + HANDLE_BYTES);
            this.phonePoint = started.slice(1 + HANDLE_BYTES);
            this.execution = new RawPhoneExecution(handle);
        } else {
            this.phonePoint = new Uint8Array(POINT_BYTES);
        }
        const payload = new Uint8Array(
            PAIRING_ID_BYTES + NONCE_BYTES + 4 + PHONE_CAPS.length + POINT_BYTES,
        );
        let offset = 0;
        payload.set(this.pairingId, offset);
        offset += PAIRING_ID_BYTES;
        payload.set(this.phoneNonce, offset);
        offset += NONCE_BYTES;
        offset = putU16(payload, offset, 1);
        offset = putU16(payload, offset, PHONE_CAPS.length);
        payload.set(PHONE_CAPS, offset);
        offset += PHONE_CAPS.length;
        payload.set(checkNotNull(this.phonePoint), offset);
        return encodePairingFrame(PairingFrameType.PA, payload);
    }

    /** Processes the client's `pairing_client_point` and answers with `cA`. */
    acceptPoint(pointFrame: Uint8Array): Uint8Array {
        const frame = decodePairingFrame(pointFrame);
        if (frame.type !== PairingFrameType.PB) throw new Error('expected pairing_client_point');
        const execution = this.execution;
        if (execution === null) throw new Error('phone execution is not started');
        this.clientPoint = frame.payload.slice();
        this.phoneConfirm = execution.exchange(frame.payload);
        return this.phoneConfirm;
    }

    /** Verifies the client's `cB` and derives the §5.1 schedule. */
    async acceptConfirmB(confirmFrame: Uint8Array): Promise<void> {
        const frame = decodePairingFrame(confirmFrame);
        if (frame.type !== PairingFrameType.CB) throw new Error('expected pairing_confirm_b');
        const execution = this.execution;
        if (execution === null) throw new Error('phone execution is not started');
        this.clientConfirm = frame.payload.slice();
        execution.confirm(frame.payload);
        const shared = execution.finish();
        this.execution = null;
        try {
            this.keys = await deriveEnrollmentKeys(shared, checkNotNull(this.context));
        } finally {
            shared.fill(0);
        }
        this.prefix = await enrollmentSessionPrefix(checkNotNull(this.context));
        this.aToBKey = await importAes(this.keys.aToBAesKey);
        this.bToAKey = await importAes(this.keys.bToAAesKey);
        this.txSequence = 0n;
        this.rxSequence = 0n;
    }

    /** Seals the phone's own `enroll_identity` (A-to-B, sequence 0). */
    async identityFrame(options: IdentityFrameOptions = {}): Promise<Uint8Array> {
        const deviceType = options.deviceTypeOk === false ? DeviceType.DESKTOP : DeviceType.PHONE;
        const role = deviceType === DeviceType.PHONE ? IdentityRole.PHONE : IdentityRole.CLIENT;
        const record = await createIdentityRecord(
            {
                installationId: this.installationId,
                label: PHONE_LABEL,
                deviceType,
                identityPubkey: this.identityPubkey,
                nonce: this.identityNonce,
            },
            role,
        );
        if (options.fingerprintOk === false) record.fingerprint[0] ^= 1;
        const body = encodeIdentityRecord(record, role);
        this.phoneIdentityBody = body.slice();
        const sealed = await sealRecord(
            checkNotNull(this.aToBKey),
            checkNotNull(this.keys).aToBNoncePrefix,
            checkNotNull(this.prefix),
            Direction.PHONE_TO_CLIENT,
            RecordType.ENROLL_IDENTITY,
            this.txSequence,
            body,
        );
        this.txSequence += 1n;
        return options.aeadOk === false ? flipLast(sealed) : sealed;
    }

    /** Opens and authenticates the client's own identity record. */
    async acceptClientIdentity(frame: Uint8Array): Promise<void> {
        const opened = await openRecord(
            checkNotNull(this.bToAKey),
            checkNotNull(this.keys).bToANoncePrefix,
            checkNotNull(this.prefix),
            Direction.CLIENT_TO_PHONE,
            this.rxSequence,
            frame,
        );
        if (opened.type !== RecordType.ENROLL_IDENTITY) {
            throw new Error('expected the client enroll_identity record');
        }
        const record = await decodeFingerprintVerifiedIdentityRecord(
            opened.body,
            IdentityRole.CLIENT,
        );
        this.clientIdentityBody = encodeIdentityRecord(record, IdentityRole.CLIENT);
        this.rxSequence += 1n;
    }

    /** `ET` over both canonical bodies and the complete SPAKE2 exchange. */
    async expectedTranscript(): Promise<Uint8Array> {
        return transcript({
            context: checkNotNull(this.context),
            phonePoint: checkNotNull(this.phonePoint),
            clientPoint: checkNotNull(this.clientPoint),
            phoneConfirm: checkNotNull(this.phoneConfirm),
            clientConfirm: checkNotNull(this.clientConfirm),
            phoneIdentityBody: checkNotNull(this.phoneIdentityBody),
            clientIdentityBody: checkNotNull(this.clientIdentityBody),
        });
    }

    private async mac(stage: number): Promise<Uint8Array> {
        const et = await this.expectedTranscript();
        const mac = await hmacSha256(
            checkNotNull(this.keys).phoneActivationKey,
            concat(et, Uint8Array.of(stage)),
        );
        et.fill(0);
        return mac;
    }

    /** The real `HMAC(K_act_phone, ET || stage)` for this connection. */
    activationMac(stage: number): Promise<Uint8Array> {
        return this.mac(stage);
    }

    async prepareFrame(options: RecordOptions = {}): Promise<Uint8Array> {
        const body = await this.mac(0);
        if (options.macOk === false) body[0] ^= 1;
        return this.protectedRecord(RecordType.ENROLLMENT_PREPARE, body, options);
    }

    async completeFrame(options: RecordOptions = {}): Promise<Uint8Array> {
        const body = await this.mac(2);
        if (options.macOk === false) body[0] ^= 1;
        return this.protectedRecord(RecordType.ACTIVATION_COMPLETE, body, options);
    }

    async appErrorFrame(code: number): Promise<Uint8Array> {
        return this.protectedRecord(RecordType.APP_ERROR, Uint8Array.of(code, 0, 0));
    }

    async strayPushFrame(): Promise<Uint8Array> {
        return this.protectedRecord(RecordType.PUSH_EVENT, new Uint8Array(32));
    }

    async protectedRecord(
        type: number,
        body: Uint8Array,
        options: RecordOptions = {},
    ): Promise<Uint8Array> {
        const prefix =
            options.sessionIdOk === false
                ? Uint8Array.of(0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff, 0x11, 0x22)
                : checkNotNull(this.prefix);
        const sealed = await sealRecord(
            checkNotNull(this.aToBKey),
            checkNotNull(this.keys).aToBNoncePrefix,
            prefix,
            Direction.PHONE_TO_CLIENT,
            type,
            this.txSequence,
            body,
        );
        this.txSequence += 1n;
        if (options.tagOk === false) sealed[sealed.length - 1] ^= 1;
        if (options.lengthOk === false) {
            const length = u16(sealed, 13);
            sealed[13] = ((length - 1) >>> BITS_PER_BYTE) & U8_MASK;
            sealed[14] = (length - 1) & U8_MASK;
        }
        return sealed;
    }

    close(): void {
        this.execution?.close();
        this.execution = null;
        if (this.keys !== null) wipeEnrollmentKeys(this.keys);
        this.keys = null;
        this.aToBKey = null;
        this.bToAKey = null;
        this.context?.fill(0);
        this.context = null;
        this.prefix?.fill(0);
        this.prefix = null;
        this.phoneIdentityBody?.fill(0);
        this.phoneIdentityBody = null;
        this.clientIdentityBody?.fill(0);
        this.clientIdentityBody = null;
        this.phonePoint?.fill(0);
        this.phonePoint = null;
        this.clientPoint?.fill(0);
        this.clientPoint = null;
        this.phoneConfirm?.fill(0);
        this.phoneConfirm = null;
        this.clientConfirm?.fill(0);
        this.clientConfirm = null;
        this.clientNonce?.fill(0);
        this.clientNonce = null;
        this.clientCaps = null;
    }
}

function checkNotNull<T>(value: T | null): T {
    if (value === null) throw new Error('phone state is incomplete');
    return value;
}
