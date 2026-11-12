// OTP2-STA-010a, OTP2-STA-010b step 0 and the §11.2 client pairing table.
//
// The client is SPAKE2 party B. This machine owns one enrollment connection:
// it verifies cA before releasing cB or deriving any enrollment key, authenticates
// the phone's `enroll_identity` before releasing its own, derives and atomically
// persists the complete public `pending` record (with `ET`) before that release,
// and emits `activation_commit` only from a prepare-verified state. It never
// routes through `SessionManager`; the connection is an enrollment connection,
// not an authenticated reconnect session.

import { equalConstantTime } from '../session/engine';
import { requireValidPoint, requireValidPointBytes } from '../session/p256';
import { openRecord, sealRecord } from '../session/record';
import { concat, hmacSha256 } from '../session/schedule';
import { Direction, RecordType } from '../session/wire';
import {
    DeviceType,
    IdentityRole,
    createIdentityRecord,
    decodeIdentityRecordForStorage,
    deriveEnrollmentKeys,
    encodeIdentityRecord,
    enrollmentSessionPrefix,
    fingerprintDisplay,
    isEnrollmentKeysWiped,
    pairFingerprint,
    transcript,
    wipeEnrollmentKeys,
    type EnrollmentKeys,
    type IdentityRecord,
} from './enrollment';
import type { PairingCore } from './core';
import type { ProofRegistry } from './proofs';

export type { PairingCore } from './core';
import {
    PREAUTH_REJECT,
    PairingFrameType,
    decodePairingFrame,
    encodePairingFrame,
    preauthReject,
} from './frames';
import {
    bytes,
    hex,
    type InstallationIdentity,
    type PendingPeer,
    type TrustedPeer,
} from '../trust/model';

/** The §11.2 client pairing states (`protocol/v1/states.json`). */
export type ClientState =
    | 'idle'
    | 'awaiting_offer'
    | 'awaiting_ca'
    | 'awaiting_phone_identity'
    | 'awaiting_prepare'
    | 'verified_prepare_ready'
    | 'awaiting_complete'
    | 'active';

/** The repository surface the enrollment flow needs; `ChromeTrustRepository` satisfies it. */
export interface ClientTrustRepository {
    identity(): Promise<InstallationIdentity>;
    peers(): Promise<TrustedPeer[]>;
    addPending(peer: PendingPeer): Promise<void>;
    confirm(peerId: string, stage: 'complete' | 'phone_active', proof: Uint8Array): Promise<void>;
    revoke(peerId: string): Promise<void>;
    /**
     * Conditional revoke used by cancel: removes the record only while it is still
     * `pending`, under the store's own lock. Returns true when the record was pending
     * and is now deleted; false when it was already recovered/absent. Repositories that
     * cannot implement the conditional write omit it and the caller falls back to a
     * read-then-revoke.
     */
    revokePending?(peerId: string): Promise<boolean>;
}

export interface ClientMachineDeps {
    core: PairingCore;
    repo: ClientTrustRepository;
    /** Session-bound proof registry: the enrollment flow registers the `complete` MAC. */
    proofs?: ProofRegistry;
    clock: () => number;
    code: string;
    label?: string;
    deviceType?: DeviceType;
    caps?: Uint8Array;
    /** The 16-byte client `pairing_request` nonce; tests inject it. */
    nonce?: () => Uint8Array;
    /** The 16-byte nonce of the client's own `enroll_identity`; tests inject it. */
    identityNonce?: () => Uint8Array;
}

/** Generic client pairing rejection. It carries no code, record or key value. */
export class ClientReject extends Error {
    constructor() {
        super();
        this.name = 'ClientReject';
    }
}

const CLIENT_TIMEOUT_MS = 30_000;
const PAIRING_ID_BYTES = 16;
const NONCE_BYTES = 16;
const POINT_BYTES = 65;
const CONFIRMATION_BYTES = 32;
const AUTHENTICATOR_BYTES = 32;
const MAX_CAPS = 16;
const MANDATORY_CAP = 0x01;
const KNOWN_CAP_MIN = 0x01;
const KNOWN_CAP_MAX = 0x03;
const CONTEXT_PREFIX = 'Veles SPAKE2 public context v1';
const DEFAULT_CAPS = Uint8Array.of(0x01, 0x03);
const DEFAULT_LABEL = 'Chrome';
const ENROLLMENT_REJECTED = 0x04;
const U8_MASK = 0xff;
const BITS_PER_BYTE = 8;
const U16_BYTES = 2;

const PRE_AUTH_STATES: ReadonlySet<ClientState> = new Set(['awaiting_offer', 'awaiting_ca']);

const PROTECTED_STATES: ReadonlySet<ClientState> = new Set([
    'awaiting_phone_identity',
    'awaiting_prepare',
    'verified_prepare_ready',
    'awaiting_complete',
    'active',
]);

const encoder = new TextEncoder();

function u16(value: Uint8Array, offset: number): number {
    return ((value[offset] & U8_MASK) << BITS_PER_BYTE) | (value[offset + 1] & U8_MASK);
}

function putU16(target: Uint8Array, offset: number, value: number): number {
    target[offset] = (value >>> BITS_PER_BYTE) & U8_MASK;
    target[offset + 1] = value & U8_MASK;
    return offset + U16_BYTES;
}

function labelBytes(value: string): Uint8Array {
    return encoder.encode(value);
}

function looksLikePreauthReject(bytes: Uint8Array): boolean {
    return bytes.length === 3 && bytes[0] === PREAUTH_REJECT && bytes[1] === 0 && bytes[2] === 0;
}

function validCaps(caps: Uint8Array): boolean {
    if (caps.length === 0 || caps.length > MAX_CAPS) return false;
    if (caps[0] !== MANDATORY_CAP) return false;
    if (!caps.every((cap) => cap >= KNOWN_CAP_MIN && cap <= KNOWN_CAP_MAX)) return false;
    return caps.every((cap, index) => index === 0 || caps[index - 1] < cap);
}

interface Offer {
    pairingId: Uint8Array;
    phoneNonce: Uint8Array;
    caps: Uint8Array;
    point: Uint8Array;
}

/** `pairing_offer`: pairing_id || phone_nonce || version || caps_len || caps || pA. */
function parseOffer(payload: Uint8Array): Offer {
    const fixed = PAIRING_ID_BYTES + NONCE_BYTES + 4;
    if (payload.length < fixed + POINT_BYTES) throw new ClientReject();
    if (u16(payload, PAIRING_ID_BYTES + NONCE_BYTES) !== 1) throw new ClientReject();
    const capsLength = u16(payload, PAIRING_ID_BYTES + NONCE_BYTES + 2);
    if (payload.length !== fixed + capsLength + POINT_BYTES) throw new ClientReject();
    const caps = payload.slice(fixed, fixed + capsLength);
    if (!validCaps(caps)) throw new ClientReject();
    const point = payload.slice(fixed + capsLength);
    if (point.length !== POINT_BYTES) throw new ClientReject();
    requireValidPointBytes(point);
    return {
        pairingId: payload.slice(0, PAIRING_ID_BYTES),
        phoneNonce: payload.slice(PAIRING_ID_BYTES, PAIRING_ID_BYTES + NONCE_BYTES),
        caps,
        point,
    };
}

/** §3.3 canonical public context (AAD); the phone derives the same bytes. */
function publicContext(
    pairingId: Uint8Array,
    phoneNonce: Uint8Array,
    clientNonce: Uint8Array,
    phoneCaps: Uint8Array,
    clientCaps: Uint8Array,
): Uint8Array {
    const prefix = labelBytes(CONTEXT_PREFIX);
    const context = new Uint8Array(
        prefix.length +
            6 +
            PAIRING_ID_BYTES +
            2 +
            NONCE_BYTES * 2 +
            4 +
            phoneCaps.length +
            clientCaps.length,
    );
    let offset = 0;
    context.set(prefix, offset);
    offset += prefix.length;
    offset = putU16(context, offset, 1);
    offset = putU16(context, offset, 1);
    offset = putU16(context, offset, 1);
    context.set(pairingId, offset);
    offset += PAIRING_ID_BYTES;
    context[offset] = 0x01;
    context[offset + 1] = 0x02;
    offset += 2;
    context.set(phoneNonce, offset);
    offset += NONCE_BYTES;
    context.set(clientNonce, offset);
    offset += NONCE_BYTES;
    offset = putU16(context, offset, phoneCaps.length);
    context.set(phoneCaps, offset);
    offset += phoneCaps.length;
    putU16(context, offset, clientCaps.length);
    context.set(clientCaps, offset + U16_BYTES);
    return context;
}

function importAes(raw: Uint8Array): Promise<CryptoKey> {
    return crypto.subtle.importKey('raw', raw, { name: 'AES-GCM' }, false, ['encrypt', 'decrypt']);
}

/** The §11.2 client state machine for one enrollment connection. */
export class ClientMachine {
    readonly #core: PairingCore;
    readonly #repo: ClientTrustRepository;
    readonly #proofs: ProofRegistry | undefined;
    readonly #clock: () => number;
    readonly #code: string;
    readonly #label: string;
    readonly #deviceType: DeviceType;
    readonly #caps: Uint8Array;
    readonly #nonce: () => Uint8Array;
    readonly #identityNonce: () => Uint8Array;

    #state: ClientState = 'idle';
    #failure = false;
    #closed = false;
    #queue: Promise<unknown> = Promise.resolve();
    #lastActivity = 0;

    #execution: ReturnType<PairingCore['start']>['execution'] | null = null;
    #keys: EnrollmentKeys | null = null;
    #aToBKey: CryptoKey | null = null;
    #bToAKey: CryptoKey | null = null;
    #prefix: Uint8Array | null = null;
    #clientNonce: Uint8Array | null = null;
    #context: Uint8Array | null = null;
    #phonePoint: Uint8Array | null = null;
    #clientPoint: Uint8Array | null = null;
    #phoneConfirm: Uint8Array | null = null;
    #clientConfirm: Uint8Array | null = null;
    #phoneBody: Uint8Array | null = null;
    #clientBody: Uint8Array | null = null;
    #et: Uint8Array | null = null;
    #pendingPeerId: string | null = null;
    #fingerprint: string | undefined;
    #rxSequence = 0n;
    #txSequence = 0n;

    constructor(deps: ClientMachineDeps) {
        this.#core = deps.core;
        this.#repo = deps.repo;
        this.#proofs = deps.proofs;
        this.#clock = deps.clock;
        this.#code = deps.code;
        this.#label = deps.label ?? DEFAULT_LABEL;
        this.#deviceType = deps.deviceType ?? DeviceType.DESKTOP;
        this.#caps = (deps.caps ?? DEFAULT_CAPS).slice();
        this.#nonce = deps.nonce ?? (() => crypto.getRandomValues(new Uint8Array(NONCE_BYTES)));
        this.#identityNonce =
            deps.identityNonce ?? (() => crypto.getRandomValues(new Uint8Array(NONCE_BYTES)));
        if (!/^[0-9]{6}$/.test(this.#code)) throw new ClientReject();
        if (!validCaps(this.#caps)) throw new ClientReject();
    }

    get state(): ClientState {
        return this.#state;
    }

    /** A visible generic failure; the caller tears the link down. */
    get failed(): boolean {
        return this.#failure;
    }

    /** True once the enrollment connection must end (failure, cancel or activation). */
    get closed(): boolean {
        return this.#closed;
    }

    /** OTP2-REC-011 display, available only after both identity records are authenticated. */
    fingerprint(): string | undefined {
        return this.#fingerprint;
    }

    /** The live key holder, for the OTP2-CRY-011/019e erasure observations. */
    enrollmentKeysForTest(): EnrollmentKeys | null {
        return this.#keys;
    }

    /** The current inbound sequence, observed by the trace runner's `advance_rx_sequence` guard. */
    rxSequenceForTest(): bigint {
        return this.#rxSequence;
    }

    /** Builds the one `pairing_request` frame; legal only from `idle`. */
    start(): Uint8Array {
        if (this.#state !== 'idle' || this.#closed) throw new ClientReject();
        const nonce = this.#nonce();
        if (nonce.length !== NONCE_BYTES) throw new ClientReject();
        this.#clientNonce = nonce;
        const payload = concat(
            nonce,
            Uint8Array.of(0, 1),
            Uint8Array.of(0, this.#caps.length),
            this.#caps,
        );
        this.#state = 'awaiting_offer';
        this.#lastActivity = this.#clock();
        return encodePairingFrame(PairingFrameType.REQUEST, payload);
    }

    onFrame(bytes: Uint8Array): Promise<Uint8Array[]> {
        return this.#serial(() => this.#onFrame(bytes));
    }

    /** Sends `activation_commit`; only a verified prepare on this connection permits it. */
    commit(): Promise<Uint8Array> {
        return this.#serial(() => this.#commit());
    }

    /**
     * User cancel: deletes this enrollment's still-pending record and erases all material.
     * Resolves false when the pending-record cleanup could not be completed, so the debug
     * caller can report it. A record already recovered to the active family is never revoked.
     */
    cancel(): Promise<boolean> {
        return this.#serial(() => this.#cancel());
    }

    /** Link loss: erases material and returns to `idle`, keeping a durable pending record. */
    onDisconnect(): Promise<void> {
        return this.#serial(() => {
            this.#eraseLive();
            this.#state = 'idle';
            this.#failure = true;
            this.#closed = true;
            this.#fingerprint = undefined;
        });
    }

    /** The §11.2 client timeout; returns true when the attempt was abandoned. */
    onTick(): Promise<boolean> {
        return this.#serial(() => {
            if (this.#state === 'idle' || this.#state === 'active') return false;
            if (this.#clock() - this.#lastActivity < CLIENT_TIMEOUT_MS) return false;
            // The client sends NO commit after a lost prepare; a durable pending
            // record survives for the recovery protocol.
            this.#eraseLive();
            this.#state = 'idle';
            this.#failure = true;
            this.#closed = true;
            this.#fingerprint = undefined;
            return true;
        });
    }

    #serial<T>(operation: () => T | Promise<T>): Promise<T> {
        const next = this.#queue.then(() => operation());
        this.#queue = next.then(
            () => undefined,
            () => undefined,
        );
        return next;
    }

    async #onFrame(bytes: Uint8Array): Promise<Uint8Array[]> {
        const preAuth = PRE_AUTH_STATES.has(this.#state);
        if (this.#state === 'idle') return [];
        try {
            if (PROTECTED_STATES.has(this.#state)) return await this.#onProtected(bytes);
            return await this.#onPlain(bytes);
        } catch {
            return preAuth ? this.#failPreAuthentication() : this.#failPostAuthentication();
        }
    }

    async #onPlain(bytes: Uint8Array): Promise<Uint8Array[]> {
        if (looksLikePreauthReject(bytes)) return this.#failPreAuthentication();
        let frame;
        try {
            frame = decodePairingFrame(bytes);
        } catch {
            return this.#failPreAuthentication();
        }
        if (this.#state === 'awaiting_offer') {
            if (frame.type !== PairingFrameType.PA) return this.#failPreAuthentication();
            return this.#onOffer(frame.payload);
        }
        if (this.#state === 'awaiting_ca') {
            if (frame.type !== PairingFrameType.CA) return this.#failPreAuthentication();
            return this.#onConfirmA(frame.payload);
        }
        // A frame in the client's own outbound direction is a role/context mismatch.
        return this.#failPreAuthentication();
    }

    async #onOffer(payload: Uint8Array): Promise<Uint8Array[]> {
        let offer: Offer;
        try {
            offer = parseOffer(payload);
        } catch {
            return this.#failPreAuthentication();
        }
        const clientNonce = this.#clientNonce;
        if (clientNonce === null) return this.#failPreAuthentication();
        const context = publicContext(
            offer.pairingId,
            offer.phoneNonce,
            clientNonce,
            offer.caps,
            this.#caps,
        );
        try {
            // SPAKE2 is started only after the offer's public point passed OTP2-CRY-005a.
            void (await requireValidPoint(offer.point));
            const started = this.#core.start(this.#code, context);
            if (started.point.length !== POINT_BYTES || started.point[0] !== 0x04) {
                started.execution.close();
                throw new ClientReject();
            }
            this.#execution = started.execution;
            this.#clientPoint = started.point.slice();
            this.#phonePoint = offer.point.slice();
            this.#context = context;
            this.#execution.exchange(offer.point);
            this.#state = 'awaiting_ca';
            this.#lastActivity = this.#clock();
            return [encodePairingFrame(PairingFrameType.PB, this.#clientPoint)];
        } catch {
            return this.#failPreAuthentication();
        }
    }

    async #onConfirmA(payload: Uint8Array): Promise<Uint8Array[]> {
        if (payload.length !== CONFIRMATION_BYTES) return this.#failPreAuthentication();
        const execution = this.#execution;
        const context = this.#context;
        if (execution === null || context === null) return this.#failPreAuthentication();
        let confirmation: Uint8Array;
        let shared: Uint8Array;
        try {
            // cA is verified here, before cB is released and before any key is derived.
            confirmation = execution.confirm(payload);
            if (confirmation.length !== CONFIRMATION_BYTES) throw new ClientReject();
            shared = execution.finish();
        } catch {
            return this.#failPreAuthentication();
        } finally {
            // `finish` consumed the Rust handle; close keeps the JS registry exact.
            execution.close();
            this.#execution = null;
        }
        try {
            this.#keys = await deriveEnrollmentKeys(shared, context);
            this.#prefix = await enrollmentSessionPrefix(context);
            this.#aToBKey = await importAes(this.#keys.aToBAesKey);
            this.#bToAKey = await importAes(this.#keys.bToAAesKey);
        } catch {
            return this.#failPostAuthentication();
        } finally {
            shared.fill(0);
        }
        this.#phoneConfirm = payload.slice();
        this.#clientConfirm = confirmation.slice();
        this.#rxSequence = 0n;
        this.#txSequence = 0n;
        this.#state = 'awaiting_phone_identity';
        this.#lastActivity = this.#clock();
        return [encodePairingFrame(PairingFrameType.CB, confirmation)];
    }

    async #onProtected(bytes: Uint8Array): Promise<Uint8Array[]> {
        if (looksLikePreauthReject(bytes)) return this.#failPostAuthentication();
        if (this.#keys === null || this.#aToBKey === null || this.#prefix === null) {
            // After activation the enrollment keys are gone; any further record is
            // unauthorized and the connection must end without touching the durable
            // active-family record or its sequence.
            return this.#rejectUnauthorized();
        }
        let opened;
        try {
            opened = await openRecord(
                this.#aToBKey,
                this.#keys.aToBNoncePrefix,
                this.#prefix,
                Direction.PHONE_TO_CLIENT,
                this.#rxSequence,
                bytes,
            );
        } catch {
            return this.#failPostAuthentication();
        }
        this.#rxSequence += 1n;
        if (
            this.#state === 'awaiting_phone_identity' &&
            opened.type === RecordType.ENROLLMENT_PREPARE
        ) {
            // The phone cannot legitimately have produced a prepare before receiving
            // the client's identity, so this is an ordering violation.
            return this.#failPostAuthentication();
        }
        switch (opened.type) {
            case RecordType.ENROLL_IDENTITY:
                return this.#onPhoneIdentity(opened.body);
            case RecordType.ENROLLMENT_PREPARE:
                return this.#onPrepare(opened.body);
            case RecordType.ACTIVATION_COMPLETE:
                return this.#onActivationComplete(opened.body);
            case RecordType.APP_ERROR:
                return this.#onAppError(opened.body);
            case RecordType.APP_HEARTBEAT:
                return opened.body.length === 0 ? [] : this.#failPostAuthentication();
            default:
                return this.#rejectUnauthorized();
        }
    }

    async #onPhoneIdentity(body: Uint8Array): Promise<Uint8Array[]> {
        if (this.#state !== 'awaiting_phone_identity') return this.#failPostAuthentication();
        let phone: IdentityRecord;
        try {
            // OTP2-REC-010/011a and OTP2-CRY-005a: structure, role-bound device type,
            // own-record fingerprint and the full P-256 check all run before the store.
            phone = await decodeIdentityRecordForStorage(
                body,
                IdentityRole.PHONE,
                async (point) => {
                    await requireValidPoint(point);
                },
            );
        } catch {
            return this.#failPostAuthentication();
        }
        let own: IdentityRecord;
        let ownBody: Uint8Array;
        let phoneBody: Uint8Array;
        let et: Uint8Array;
        try {
            const identity = await this.#repo.identity();
            own = await createIdentityRecord(
                {
                    installationId: bytes(identity.installationId, NONCE_BYTES),
                    label: this.#label,
                    deviceType: this.#deviceType,
                    identityPubkey: bytes(identity.publicKey, POINT_BYTES),
                    nonce: this.#identityNonce(),
                },
                IdentityRole.CLIENT,
            );
            ownBody = encodeIdentityRecord(own, IdentityRole.CLIENT);
            phoneBody = encodeIdentityRecord(phone, IdentityRole.PHONE);
            et = await transcript({
                context: this.#requiredContext(),
                phonePoint: this.#requiredPoint(this.#phonePoint),
                clientPoint: this.#requiredPoint(this.#clientPoint),
                phoneConfirm: this.#requiredConfirmation(this.#phoneConfirm),
                clientConfirm: this.#requiredConfirmation(this.#clientConfirm),
                phoneIdentityBody: phoneBody,
                clientIdentityBody: ownBody,
            });
        } catch {
            return this.#failPostAuthentication();
        }
        try {
            // OTP2-STA-010a: the COMPLETE public pending record is durable before the
            // client's own identity is released. A duplicate installation ID throws
            // here and takes exactly this persist-failure path.
            await this.#repo.addPending({
                installationId: hex(phone.installationId),
                publicKey: hex(phone.identityPubkey),
                label: phone.label,
                deviceType: phone.deviceType,
                enrollmentTranscript: hex(et),
            });
        } catch {
            ownBody.fill(0);
            phoneBody.fill(0);
            et.fill(0);
            return this.#failPostAuthentication();
        }
        this.#phoneBody = phoneBody;
        this.#clientBody = ownBody;
        this.#et = et;
        this.#pendingPeerId = hex(phone.installationId);
        try {
            this.#fingerprint = fingerprintDisplay(
                await pairFingerprint(phone.identityPubkey, own.identityPubkey),
            );
            const frame = await sealRecord(
                this.#requiredKey(this.#bToAKey),
                this.#requiredKeys().bToANoncePrefix,
                this.#requiredPrefix(),
                Direction.CLIENT_TO_PHONE,
                RecordType.ENROLL_IDENTITY,
                this.#txSequence,
                ownBody,
            );
            this.#txSequence += 1n;
            this.#state = 'awaiting_prepare';
            this.#lastActivity = this.#clock();
            return [frame];
        } catch {
            return this.#failPostAuthentication();
        }
    }

    async #onPrepare(body: Uint8Array): Promise<Uint8Array[]> {
        if (this.#state !== 'awaiting_prepare') return this.#failPostAuthentication();
        if (body.length !== AUTHENTICATOR_BYTES) return this.#failPostAuthentication();
        let expected: Uint8Array;
        try {
            expected = await hmacSha256(
                this.#requiredKeys().phoneActivationKey,
                concat(this.#requiredEt(), Uint8Array.of(0)),
            );
        } catch {
            return this.#failPostAuthentication();
        }
        if (!equalConstantTime(expected, body)) {
            expected.fill(0);
            await this.#deletePendingRecord();
            return this.#failPostAuthentication();
        }
        expected.fill(0);
        this.#state = 'verified_prepare_ready';
        this.#lastActivity = this.#clock();
        return [];
    }

    async #onActivationComplete(body: Uint8Array): Promise<Uint8Array[]> {
        if (this.#state !== 'awaiting_complete') return this.#failPostAuthentication();
        if (body.length !== AUTHENTICATOR_BYTES) return this.#failPostAuthentication();
        let expected: Uint8Array;
        try {
            expected = await hmacSha256(
                this.#requiredKeys().phoneActivationKey,
                concat(this.#requiredEt(), Uint8Array.of(2)),
            );
        } catch {
            return this.#failPostAuthentication();
        }
        if (!equalConstantTime(expected, body)) {
            expected.fill(0);
            // A MAC failure never leaves a half-activated trust state; the pending
            // record stays for the signed recovery protocol.
            return this.#failPostAuthentication();
        }
        expected.fill(0);
        const peerId = this.#pendingPeerId;
        const prefix = this.#prefix;
        if (peerId === null || prefix === null) return this.#failPostAuthentication();
        try {
            // The repository verifier is the session-bound proof registry filled here with the
            // MAC this enrollment connection just verified; it receives the real received MAC.
            this.#proofs?.register(hex(prefix), [
                { peerId, stage: 'complete', proof: body.slice() },
            ]);
            await this.#repo.confirm(peerId, 'complete', body.slice());
        } catch {
            this.#proofs?.revoke(hex(prefix));
            return this.#failPostAuthentication();
        }
        this.#pendingPeerId = null;
        try {
            const ack = await hmacSha256(
                this.#requiredKeys().clientActivationKey,
                concat(this.#requiredEt(), Uint8Array.of(3)),
            );
            const frame = await sealRecord(
                this.#requiredKey(this.#bToAKey),
                this.#requiredKeys().bToANoncePrefix,
                this.#requiredPrefix(),
                Direction.CLIENT_TO_PHONE,
                RecordType.ACTIVATION_ACK,
                this.#txSequence,
                ack,
            );
            ack.fill(0);
            this.#txSequence += 1n;
            // OTP2-CRY-019e: the client erases every enrollment key immediately
            // after sending the ack; only the public fingerprint remains.
            this.#eraseLive();
            this.#state = 'active';
            this.#closed = true;
            this.#lastActivity = this.#clock();
            return [frame];
        } catch {
            return this.#failPostAuthentication();
        }
    }

    async #onAppError(body: Uint8Array): Promise<Uint8Array[]> {
        if (body.length < 1) return this.#failPostAuthentication();
        if (body[0] === ENROLLMENT_REJECTED) {
            // OTP2-STA-012: delete this enrollment's pending record, erase the
            // pairing state and show the generic failure.
            await this.#deletePendingRecord();
            return this.#failPostAuthentication();
        }
        return this.#failPostAuthentication();
    }

    async #commit(): Promise<Uint8Array> {
        if (this.#state !== 'verified_prepare_ready') throw new ClientReject();
        try {
            const mac = await hmacSha256(
                this.#requiredKeys().clientActivationKey,
                concat(this.#requiredEt(), Uint8Array.of(1)),
            );
            const frame = await sealRecord(
                this.#requiredKey(this.#bToAKey),
                this.#requiredKeys().bToANoncePrefix,
                this.#requiredPrefix(),
                Direction.CLIENT_TO_PHONE,
                RecordType.ACTIVATION_COMMIT,
                this.#txSequence,
                mac,
            );
            mac.fill(0);
            this.#txSequence += 1n;
            this.#state = 'awaiting_complete';
            this.#lastActivity = this.#clock();
            return frame;
        } catch {
            this.#eraseLive();
            this.#state = 'idle';
            this.#failure = true;
            this.#closed = true;
            this.#fingerprint = undefined;
            throw new ClientReject();
        }
    }

    async #cancel(): Promise<boolean> {
        const peerId = this.#pendingPeerId;
        this.#eraseLive();
        this.#state = 'idle';
        this.#failure = false;
        this.#closed = true;
        this.#fingerprint = undefined;
        this.#pendingPeerId = null;
        if (peerId === null) return true;
        try {
            const conditional = this.#repo.revokePending;
            if (conditional !== undefined) {
                // The conditional write runs entirely under the trust store's lock, so a
                // concurrent recovery confirm cannot turn a just-read `pending` record
                // into active between the read and the delete. A non-pending record is
                // nothing to clean up, not a cancel failure.
                await conditional.call(this.#repo, peerId);
                return true;
            }
            // A later tab teardown or startPairing can reach an abandoned link whose
            // pending record a recovery session has since authenticated. Revoke only the
            // record this enrollment still owns in `pending`; an active-family record is
            // a legitimately recovered peer.
            const current = (await this.#repo.peers()).find(
                (peer) => peer.installationId === peerId,
            );
            if (current?.state !== 'pending') return true;
            await this.#repo.revoke(peerId);
            return true;
        } catch {
            // Local state is erased; a store failure leaves only an inactive
            // record, never a live key or an open connection. The Boolean lets the
            // debug caller surface the cleanup failure.
            return false;
        }
    }

    #failPreAuthentication(): Uint8Array[] {
        this.#eraseLive();
        this.#state = 'idle';
        this.#failure = true;
        this.#closed = true;
        this.#fingerprint = undefined;
        return [preauthReject()];
    }

    #failPostAuthentication(): Uint8Array[] {
        this.#eraseLive();
        this.#state = 'idle';
        this.#failure = true;
        this.#closed = true;
        this.#fingerprint = undefined;
        return [];
    }

    #rejectUnauthorized(): Uint8Array[] {
        // The active-family mirror and its public fingerprint survive a record
        // refused after activation; every pre-active unauthorized record is a
        // failed pairing and clears it.
        const keepFingerprint = this.#state === 'active';
        this.#eraseLive();
        this.#state = 'idle';
        this.#failure = true;
        this.#closed = true;
        if (!keepFingerprint) this.#fingerprint = undefined;
        return [];
    }

    async #deletePendingRecord(): Promise<void> {
        const peerId = this.#pendingPeerId;
        this.#pendingPeerId = null;
        if (peerId === null) return;
        try {
            await this.#repo.revoke(peerId);
        } catch {
            // The record stays inactive; no enrollment material survives either way.
        }
    }

    #eraseLive(): void {
        this.#execution?.close();
        this.#execution = null;
        if (this.#keys !== null) {
            if (!isEnrollmentKeysWiped(this.#keys)) wipeEnrollmentKeys(this.#keys);
            this.#keys = null;
        }
        this.#aToBKey = null;
        this.#bToAKey = null;
        this.#prefix?.fill(0);
        this.#prefix = null;
        this.#context?.fill(0);
        this.#context = null;
        this.#phonePoint?.fill(0);
        this.#phonePoint = null;
        this.#clientPoint?.fill(0);
        this.#clientPoint = null;
        this.#phoneConfirm?.fill(0);
        this.#phoneConfirm = null;
        this.#clientConfirm?.fill(0);
        this.#clientConfirm = null;
        this.#phoneBody?.fill(0);
        this.#phoneBody = null;
        this.#clientBody?.fill(0);
        this.#clientBody = null;
        this.#et?.fill(0);
        this.#et = null;
        this.#clientNonce?.fill(0);
        this.#clientNonce = null;
    }

    #requiredKeys(): EnrollmentKeys {
        if (this.#keys === null) throw new ClientReject();
        return this.#keys;
    }

    #requiredKey(key: CryptoKey | null): CryptoKey {
        if (key === null) throw new ClientReject();
        return key;
    }

    #requiredPrefix(): Uint8Array {
        if (this.#prefix === null) throw new ClientReject();
        return this.#prefix;
    }

    #requiredContext(): Uint8Array {
        if (this.#context === null) throw new ClientReject();
        return this.#context;
    }

    #requiredEt(): Uint8Array {
        if (this.#et === null) throw new ClientReject();
        return this.#et;
    }

    #requiredPoint(point: Uint8Array | null): Uint8Array {
        if (point === null) throw new ClientReject();
        return point;
    }

    #requiredConfirmation(confirmation: Uint8Array | null): Uint8Array {
        if (confirmation === null) throw new ClientReject();
        return confirmation;
    }
}
