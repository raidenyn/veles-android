import { verifyP1363 } from './ecdsa';
import { decodeFrame, decodeSignatureFrame, encodeFrame, encodeSignatureFrame } from './frames';
import { LOCAL_CAPS, decodeHello, encodeHello, type ReconnectHello } from './hello';
import {
    HANDSHAKE_TIMEOUT_MS,
    HEARTBEAT_MIN_INTERVAL_MS,
    HEARTBEAT_SILENCE_MS,
    HEARTBEAT_TERMINATE_THRESHOLD,
    HEARTBEAT_WINDOW_MS,
    IDLE_TIMEOUT_MS,
    SEQUENCE_MAX,
} from './limits';
import { requireValidPoint } from './p256';
import { authorized } from './policy';
import { RECORD_MAX_CIPHERTEXT_BYTES, openRecord, sealRecord, type OpenedRecord } from './record';
import { SessionReject } from './reject';
import { isRemovalRecord } from './removal';
import {
    confirmation,
    confirmInput,
    deriveKeyMaterial,
    importKeys,
    rbase as reconnectRbase,
    type ReconnectKeyMaterial,
    type ReconnectKeys,
    type RecoveryKeys,
} from './schedule';
import {
    AppErrorCode,
    Direction,
    FrameType,
    RecordType,
    Role,
    SessionClass,
    frameTypeFromWire,
} from './wire';

// Client-role reconnect engine: bytes in, SessionEffects out (veles-v1.md §11.3
// client rows).
//
// The engine is a pure state machine that mirrors the accepted Kotlin
// `PhoneSessionEngine`. Time, randomness, the identity signer and the trust
// lookup are injected. All mutable state lives behind one private `#state`
// object: the connector's per-session queue (Task 8) is the only concurrency
// boundary, and every post-await commit re-checks that `#state` is still the
// same object, so a teardown is final even while crypto is pending.
//
// The client sends its hello first, verifies the phone signature against the
// stored key of the hello's installation id, and only reports `authenticated`
// once the phone's confirm has verified AND its own confirm send has completed
// with `ok` — whichever of the two finishes second.

/** Local trust-record state that the client can observe for its selected peer. */
export type PhoneRecordState = 'pending' | 'active_activation_pending' | 'active';

/** The phone's verified `peer_state` byte. */
export const PEER_STATE_ACTIVE = 0x01;
const PEER_STATE_BELOW_ACTIVE = 0x02;

const CONFIRM_SEQUENCE = 0n;
const CONFIRM_BODY_BYTES = 33;
const HASH_BYTES = 32;
const SESSION_PREFIX_BYTES = 8;
const NONCE_BYTES = 16;
const SIGNATURE_BYTES = 64;
const SHARED_SECRET_BYTES = 32;

const STAGE_UNEXPECTED_FRAME = 'unexpected_frame';
const STAGE_RECORD_INVALID = 'record_invalid';
const STAGE_CONFIRM_REQUIRED = 'confirm_required';
const STAGE_CONFIRM_INVALID = 'confirm_invalid';
const STAGE_SEND_FAILED = 'send_failed';
const STAGE_HANDSHAKE_TIMEOUT = 'handshake_timeout';
const STAGE_IDLE = 'idle';
const STAGE_HEARTBEAT = 'heartbeat';
const STAGE_APP_ERROR = 'app_error';
const STAGE_AUTHORIZATION = 'authorization';
const STAGE_UNHANDLED = 'unhandled';
const STAGE_SEQUENCE = 'sequence';
const STAGE_DEPENDENCY = 'dependency';
/** The handler asked for the reply-then-close outcome (design D4). */
const STAGE_CLOSE_AFTER_REPLY = 'close_after_reply';
/**
 * The terminal stage reported when a removal record ended the session (design D4, ruling R6).
 * The session owner maps exactly this stage to the removal outcome `removed`.
 */
export const STAGE_REMOVAL = 'removal';

/** The fixed facts of one authenticated session (OTP-10 plan 9b). */
export interface SessionContext {
    sessionIdPrefix: Uint8Array;
    sessionClass: SessionClass;
    /** The peer installation id, hex-encoded exactly as the trust store spells it. */
    peerId: string;
    /** The public enrollment transcript the record carried, or null. */
    et: Uint8Array | null;
    /** The `peer_state` the peer reported in its `reconnect_confirm`. */
    peerState: number;
    /**
     * The live OTP2-CRY-019f recovery holder for a non-full class, or null when the record
     * carried no `ET` and whenever the class is `full` (its holder has just been zeroed).
     */
    recovery: RecoveryKeys | null;
}

/** One record a handler owes the peer as soon as the session is open (OTP2-STA-010b). */
export interface UnpromptedApplication {
    type: number;
    body: Uint8Array;
}

/** Application records that have a handler. An authorized record without one fails closed. */
export interface ApplicationHandler {
    handles(type: number): boolean;
    handle(
        type: number,
        body: Uint8Array,
        reply: (type: number, body: Uint8Array) => Promise<void>,
    ): Promise<void>;
    /**
     * Optional per-session hook: the engine asks once, when the class is fixed, and uses only
     * the returned instance for this session's records. The default keeps the same instance.
     *
     * A handler receives the context on `authenticated`; the session is open at that point, so
     * its owner may send an unprompted record through `sendApplication` without any inbound
     * record (OTP-10 plan 9b).
     */
    forSession?(context: SessionContext): ApplicationHandler;

    /**
     * The explicit reply-then-close outcome (design D4): true means the engine seals and
     * delivers this handler's buffered replies, tears the engine down and emits the terminal
     * close effect, so the reply is the last record in that direction and nothing is accepted
     * afterwards. A handler must never tear the session down itself or await its own
     * generation; it returns this outcome instead.
     */
    closeAfterReply?(type: number): boolean;

    /**
     * Records the session-scoped handler owes as soon as the gate opens (OTP2-STA-010b steps
     * 0 and 5). The engine only reports them on the `authenticated` effect; the session owner
     * sends each one after it handled the effect.
     */
    unprompted?(): UnpromptedApplication[] | Promise<UnpromptedApplication[]>;
}

/** Effects that a session host turns into transport writes, host callbacks and teardown. */
export type SessionEffect =
    | { kind: 'send'; bytes: Uint8Array; completionToken?: number }
    | {
          kind: 'authenticated';
          sessionClass: SessionClass;
          peerId: string;
          sessionIdPrefix: Uint8Array;
          /** Present only when the session owes an unprompted record at open. */
          unprompted?: UnpromptedApplication[];
      }
    | { kind: 'deliver'; type: number; body: Uint8Array }
    /**
     * The terminal stage. `holdForDelivery` is set only when this effect ends a successful
     * `removal_request` reply-then-close: the engine has already torn its state down, so the
     * owner needs the mark to defer just the transport release of the delivery (design D4).
     */
    | { kind: 'terminated'; reason: string; holdForDelivery?: true }
    | { kind: 'close' };

/** Everything the client engine reads or writes outside its own state. */
export interface ClientEngineDeps {
    localInstallationId: Uint8Array;
    sign(rbase: Uint8Array): Promise<Uint8Array>;
    /** Always set: the intended trust-record id. A phone hello naming any other id fails before signing. */
    expectedPeerId: string;
    /** Generation fence, checked immediately before `upgradeMirror`. */
    isCurrent(): boolean;
    lookupPhone(id: string): Promise<PhoneRecord | undefined>;
    /** Wraps `repository.confirm(peerId, 'phone_active', proof)` with a session-bound verifier. */
    upgradeMirror(peerId: string, proof: Uint8Array): Promise<void>;
    now(): number;
    ephemeral?(): Promise<CryptoKeyPair>;
    nonce?(): Uint8Array;
    handler?: ApplicationHandler;
}

type Phase =
    | 'idle'
    | 'awaiting_phone_hello'
    | 'awaiting_phone_signature'
    | 'awaiting_phone_confirm'
    | 'open'
    | 'closed';

interface PhoneRecord {
    publicKey: Uint8Array;
    state: PhoneRecordState;
    /**
     * The public canonical enrollment transcript `ET` (OTP2-CRY-020b) of the
     * pending, committed or active record. Present means the engine derives the
     * OTP2-CRY-019f recovery activation keys.
     */
    et?: Uint8Array;
}

/**
 * Every mutable field of the engine. Teardown replaces the whole object, so a
 * resumed operation that still holds the previous object observes the
 * replacement and installs nothing.
 */
interface EngineState {
    phase: Phase;
    peerId: string | null;
    peerRecord: PhoneRecord | null;
    ephemeral: CryptoKeyPair | null;
    localNonce: Uint8Array | null;
    clientHello: Uint8Array | null;
    phoneHello: ReconnectHello | null;
    transcript: Uint8Array | null;
    phoneSignature: Uint8Array | null;
    clientSignature: Uint8Array | null;
    keys: ReconnectKeys | null;
    sessionIdPrefix: Uint8Array | null;
    sessionClass: SessionClass | null;
    peerState: number;
    confirmInput: Uint8Array | null;
    confirmToken: number | null;
    confirmSendComplete: boolean;
    peerConfirmVerified: boolean;
    /**
     * The first phone `reconnect_confirm` record whose AEAD tag, session
     * sequence and HMAC all verified. It is written once and never replaced, so
     * a later inbound ciphertext can never become another session's proof.
     */
    verifiedPhoneConfirm: Uint8Array | null;
    /** True only between that verification and the fixed session class. */
    phoneConfirmFinalizing: boolean;
    authenticated: boolean;
    /** The session-scoped handler, derived once the session id prefix is fixed. */
    handler: ApplicationHandler | null;
    nextToken: number;
    outboundSequence: bigint;
    inboundSequence: bigint;
    outboundExhausted: boolean;
    inboundExhausted: boolean;
    lastActivityAt: number;
    lastOutboundAt: number;
    lastHeartbeatSentAt: number | null;
    receivedHeartbeats: number[];
    handshakeDeadlineAt: number;
    /**
     * The requester's removal-pending guard (design D4, OTP2-REC-013). Set by the owner right
     * after it sealed `removal_request`; teardown clears it with the rest of the state.
     */
    removalPending: boolean;
}

function freshState(phase: Phase): EngineState {
    return {
        phase,
        peerId: null,
        peerRecord: null,
        ephemeral: null,
        localNonce: null,
        clientHello: null,
        phoneHello: null,
        transcript: null,
        phoneSignature: null,
        clientSignature: null,
        keys: null,
        sessionIdPrefix: null,
        sessionClass: null,
        peerState: 0,
        confirmInput: null,
        confirmToken: null,
        confirmSendComplete: false,
        peerConfirmVerified: false,
        verifiedPhoneConfirm: null,
        phoneConfirmFinalizing: false,
        authenticated: false,
        handler: null,
        nextToken: 0,
        outboundSequence: 0n,
        inboundSequence: 0n,
        outboundExhausted: false,
        inboundExhausted: false,
        lastActivityAt: 0,
        lastOutboundAt: 0,
        lastHeartbeatSentAt: null,
        receivedHeartbeats: [],
        handshakeDeadlineAt: 0,
        removalPending: false,
    };
}

function hexOf(value: Uint8Array): string {
    return Array.from(value, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

function concat(...parts: Uint8Array[]): Uint8Array {
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

/**
 * Zeros raw key material that never became a `ReconnectKeys`. Once
 * `importKeys` succeeds the caller must stop using this: the returned keys own
 * the non-AES arrays.
 */
function eraseKeyMaterial(material: ReconnectKeyMaterial | null): void {
    if (material === null) return;
    material.c2pKey.fill(0);
    material.c2pPrefix.fill(0);
    material.clientConfirmKey.fill(0);
    material.p2cKey.fill(0);
    material.p2cPrefix.fill(0);
    material.phoneConfirmKey.fill(0);
    material.sessionId.fill(0);
    eraseRecoveryKeys(material.recovery);
}

/** OTP2-CRY-019f: zero both recovery keys in place, keeping the holder observable. */
function eraseRecoveryKeys(recovery: RecoveryKeys | null): void {
    if (recovery === null) return;
    recovery.kRecClient.fill(0);
    recovery.kRecPhone.fill(0);
}

/** Zeros every engine-owned key array. The AES `CryptoKey`s are not zeroable. */
function eraseReconnectKeys(keys: ReconnectKeys | null): void {
    if (keys === null) return;
    keys.c2pPrefix.fill(0);
    keys.clientConfirmKey.fill(0);
    keys.p2cPrefix.fill(0);
    keys.phoneConfirmKey.fill(0);
    keys.sessionId.fill(0);
    eraseRecoveryKeys(keys.recovery);
}

/** Constant-time byte equality for MACs and fingerprints (OTP2-REC-011a). */
export function equalConstantTime(left: Uint8Array, right: Uint8Array): boolean {
    if (left.length !== right.length) return false;
    let difference = 0;
    for (let index = 0; index < left.length; index += 1) {
        difference |= left[index] ^ right[index];
    }
    return difference === 0;
}

function randomNonce(): Uint8Array {
    return crypto.getRandomValues(new Uint8Array(NONCE_BYTES));
}

function generateEphemeral(): Promise<CryptoKeyPair> {
    return crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, true, ['deriveBits']);
}

async function exportPoint(keyPair: CryptoKeyPair): Promise<Uint8Array> {
    return new Uint8Array(await crypto.subtle.exportKey('raw', keyPair.publicKey));
}

async function deriveSharedSecret(
    privateKey: CryptoKey,
    peerPoint: Uint8Array,
): Promise<Uint8Array> {
    const publicKey = await requireValidPoint(peerPoint);
    const shared = new Uint8Array(
        await crypto.subtle.deriveBits({ name: 'ECDH', public: publicKey }, privateKey, 256),
    );
    if (shared.length !== SHARED_SECRET_BYTES || shared.every((byte) => byte === 0)) {
        shared.fill(0);
        throw new SessionReject();
    }
    return shared;
}

/** One generic rejection for every §7.3/§11.4 authorization failure. */
function reject(): never {
    throw new SessionReject();
}

/**
 * A send whose sequence (and so its AES-GCM nonce) was already reserved but
 * whose record never completed — either the seal failed or the deadline
 * passed while it was sealing. The nonce may have reached the wire, so the
 * caller must tear the session down instead of reusing or rolling it back.
 */
class SealFailure extends Error {
    constructor() {
        super('seal failed after sequence reservation');
        this.name = 'SealFailure';
    }
}

function tryOpen(
    keys: ReconnectKeys,
    prefix: Uint8Array,
    expectedSequence: bigint,
    record: Uint8Array,
): Promise<OpenedRecord> {
    return openRecord(
        keys.p2cKey,
        keys.p2cPrefix,
        prefix,
        Direction.PHONE_TO_CLIENT,
        expectedSequence,
        record,
    );
}

/** True when `type` belongs to the client's active family (`active` or its mirror). */
function isActiveFamily(state: PhoneRecordState): boolean {
    return state === 'active' || state === 'active_activation_pending';
}

/**
 * The requester's removal fencing (design D4, OTP2-REC-013). While a response is awaited only
 * `removal_response` may reach the handler (`app_heartbeat` and `app_error` are handled before
 * this check), and a response without the mark is unsolicited: either way the session ends and
 * nothing is deleted.
 */
function acceptsDuringRemoval(pending: boolean, type: number): boolean {
    if (type === RecordType.REMOVAL_RESPONSE) return pending;
    return !pending;
}

/**
 * Design D4/R6: a removal record type has its own terminal stage, so the owner can tell a mutual
 * removal from any other close-after-reply handler.
 */
function terminalStage(terminalError: boolean, type: number): string {
    if (terminalError) return STAGE_APP_ERROR;
    return isRemovalRecord(type) ? STAGE_REMOVAL : STAGE_CLOSE_AFTER_REPLY;
}

/**
 * The §11.3 client class-selection rows. The mirror upgrade is applied first,
 * so `active_activation_pending` only reaches here without a proof (peer_state
 * `0x02`) or after a stale-generation refusal, and both stay recovery-only.
 */
function selectSessionClass(state: PhoneRecordState, peerState: number): SessionClass {
    if (state !== 'active') return SessionClass.ACTIVATION_RECOVERY;
    return peerState === PEER_STATE_ACTIVE ? SessionClass.FULL : SessionClass.FULL_PENDING_PEER;
}

export class ClientSessionEngine {
    readonly #deps: ClientEngineDeps;
    readonly #localInstallationId: Uint8Array;
    #state: EngineState = freshState('idle');
    /** Pre-teardown counter snapshot, test-only (Task 10 counter observations). */
    #inboundAtTeardown = 0n;
    #outboundAtTeardown = 0n;
    /**
     * A handler outcome the engine sealed but could not report because an external teardown
     * replaced the state first (design D4). Read once by the session owner.
     */
    #droppedTerminalStage: string | null = null;

    constructor(deps: ClientEngineDeps) {
        this.#deps = deps;
        this.#localInstallationId = deps.localInstallationId.slice();
    }

    get isOpen(): boolean {
        return this.#state.phase === 'open';
    }

    /** The class fixed at the authentication gate, or null before it and after teardown. */
    get sessionClass(): SessionClass | null {
        return this.#state.sessionClass;
    }

    /** A copy of the fixed 8-byte session id prefix, or null before the gate and after teardown. */
    get sessionIdPrefix(): Uint8Array | null {
        const prefix = this.#state.sessionIdPrefix;
        return prefix === null ? null : prefix.slice();
    }

    /**
     * The first fully verified phone `reconnect_confirm` record of this
     * generation, byte-for-byte, or `null` before verification and after
     * teardown. The connector's trust verifier binds its stage proof to exactly
     * these bytes; the engine is the only component that decides they are valid.
     */
    get verifiedPhoneConfirm(): Uint8Array | null {
        const confirm = this.#state.verifiedPhoneConfirm;
        return confirm === null ? null : confirm.slice();
    }

    /**
     * True only while the phone confirm has passed every check and the session
     * class has not been fixed yet. Nothing outside that finalization window can
     * satisfy a `phone_active` proof.
     */
    get phoneConfirmFinalizing(): boolean {
        return this.#state.phoneConfirmFinalizing;
    }

    /**
     * The `peer_state` byte of the accepted phone confirm, or `null` before a
     * verification and after teardown. The `phone_active` verifier additionally
     * requires {@link PEER_STATE_ACTIVE}: only an active proof may upgrade a mirror.
     */
    get verifiedPeerState(): number | null {
        return this.#state.peerConfirmVerified ? this.#state.peerState : null;
    }

    /** Sends the client hello. Called once per connection generation. */
    async start(): Promise<SessionEffect[]> {
        const state = this.#state;
        if (state.phase !== 'idle') return [];
        const now = this.#deps.now();
        let ephemeral: CryptoKeyPair;
        let nonceValue: Uint8Array;
        let helloPayload: Uint8Array;
        let helloFrame: Uint8Array;
        try {
            ephemeral = await (this.#deps.ephemeral?.() ?? generateEphemeral());
            nonceValue = (this.#deps.nonce?.() ?? randomNonce()).slice();
            helloPayload = encodeHello({
                installationId: this.#localInstallationId,
                ephemeralPublicKey: await exportPoint(ephemeral),
                nonce: nonceValue,
                role: Role.CLIENT,
                caps: LOCAL_CAPS.slice(),
            });
            helloFrame = encodeFrame(FrameType.RECONNECT_HELLO, helloPayload);
        } catch {
            if (this.#state !== state) return [];
            return this.#terminate(STAGE_DEPENDENCY);
        }
        if (this.#state !== state) return [];
        state.ephemeral = ephemeral;
        state.localNonce = nonceValue;
        state.clientHello = helloPayload;
        state.phase = 'awaiting_phone_hello';
        state.handshakeDeadlineAt = now + HANDSHAKE_TIMEOUT_MS;
        return [{ kind: 'send', bytes: helloFrame }];
    }

    async onInbound(bytes: Uint8Array): Promise<SessionEffect[]> {
        const now = this.#deps.now();
        const expiry = this.#expiryFailure(now);
        if (expiry !== null) return this.#terminate(expiry);
        const state = this.#state;
        if (state.phase === 'closed' || state.phase === 'idle') return [];
        if (this.#exhausted(state)) return this.#terminate(STAGE_SEQUENCE);
        if (state.phase !== 'awaiting_phone_hello' && this.#isHelloFrame(bytes)) {
            return this.#failPreAuth();
        }
        try {
            switch (state.phase) {
                case 'awaiting_phone_hello':
                    return await this.#onPhoneHello(state, bytes);
                case 'awaiting_phone_signature':
                    return await this.#onPhoneSignature(state, bytes);
                case 'awaiting_phone_confirm':
                    return await this.#onPhoneConfirm(state, bytes);
                case 'open':
                    return await this.#onProtectedRecord(state, bytes);
                default:
                    return [];
            }
        } catch {
            // Fail closed on every dependency or decoder exception. A teardown
            // that replaced the state is final and reports nothing.
            if (this.#state !== state) return [];
            return this.#terminate(STAGE_DEPENDENCY);
        }
    }

    async onSendComplete(token: number, ok: boolean): Promise<SessionEffect[]> {
        const state = this.#state;
        if (state.phase === 'closed') return [];
        const expiry = this.#expiryFailure(this.#deps.now());
        if (expiry !== null) return this.#terminate(expiry);
        if (this.#exhausted(state)) return this.#terminate(STAGE_SEQUENCE);
        if (state.authenticated) return [];
        if (token !== state.confirmToken) return [];
        if (!ok) return this.#terminate(STAGE_SEND_FAILED);
        state.confirmSendComplete = true;
        if (!state.peerConfirmVerified) return [];
        return this.#authenticate(state);
    }

    async sendApplication(type: number, body: Uint8Array): Promise<SessionEffect[]> {
        const state = this.#state;
        if (state.phase !== 'open') throw new SessionReject();
        const now = this.#deps.now();
        const expiry = this.#expiryFailure(now);
        if (expiry !== null) return this.#terminate(expiry);
        if (this.#exhausted(state)) return this.#terminate(STAGE_SEQUENCE);
        // OTP2-REC-013: after `removal_request`, any application send fails the session, so no
        // further record flows while the response is awaited.
        if (state.removalPending) return this.#terminate(STAGE_AUTHORIZATION);
        let record: Uint8Array | null;
        try {
            record = await this.#sealApplication(state, type, body);
        } catch (error) {
            if (this.#state !== state) return [];
            const lateExpiry = this.#expiryFailure(this.#deps.now());
            if (lateExpiry !== null) return this.#terminate(lateExpiry);
            if (error instanceof SealFailure) return this.#terminate(STAGE_DEPENDENCY);
            // A pre-reservation rejection (for example an oversize body or an
            // unauthorized type) leaves the session and its sequence intact.
            throw error;
        }
        if (this.#state !== state) return [];
        if (record === null) return [];
        if (type === RecordType.APP_ERROR) {
            this.#teardownInternal();
            return [
                { kind: 'send', bytes: record },
                { kind: 'terminated', reason: STAGE_APP_ERROR },
                { kind: 'close' },
            ];
        }
        return [{ kind: 'send', bytes: record }];
    }

    /**
     * Sets the requester's removal-pending guard (design D4, OTP2-REC-013). The owner sets it on
     * this session only, immediately after it sealed `removal_request` and inside the same
     * serialized step, so no response can be processed before the guard is in place.
     */
    setRemovalPending(on: boolean): void {
        this.#state.removalPending = on;
    }

    /** Deadlines only. The client's heartbeat driver runs while processing inbound events. */
    async onTick(): Promise<SessionEffect[]> {
        const state = this.#state;
        if (state.phase === 'closed') return [];
        const expiry = this.#expiryFailure(this.#deps.now());
        if (expiry !== null) return this.#terminate(expiry);
        if (this.#exhausted(state)) return this.#terminate(STAGE_SEQUENCE);
        return [];
    }

    teardown(): void {
        this.#teardownInternal();
    }

    /**
     * The terminal stage of a handler outcome whose effects an external teardown dropped, or
     * `null`. Read once: a removal handler that completed still deleted the local record even
     * though the generation ended before its terminal effects could be applied (design D4).
     */
    takeDroppedTerminalStage(): string | null {
        const stage = this.#droppedTerminalStage;
        this.#droppedTerminalStage = null;
        return stage;
    }

    /**
     * Test-only counter seam (Task 10 conformance runner): positions the next
     * expected inbound sequence and the next outbound sequence without
     * advancing the handshake.
     */
    _seekSequences(rx: bigint, tx: bigint): void {
        const state = this.#state;
        state.inboundSequence = rx;
        state.outboundSequence = tx;
        state.inboundExhausted = false;
        state.outboundExhausted = false;
    }

    /** Test-only OTP2-STA-015 erasure observation: final state with no key material left. */
    get _keysErased(): boolean {
        return this.#state.phase === 'closed' && this.#state.keys === null;
    }

    /**
     * Test-only OTP2-CRY-019f observation: the live recovery holder of the derived keys. It is
     * the same holder the session context carries for a non-full class, so a test can take it
     * before the gate and assert its bytes are zeroed once the class is `full`.
     */
    get _recoveryKeys(): RecoveryKeys | null {
        return this.#state.keys?.recovery ?? null;
    }

    /** Test-only read of the next expected inbound sequence (Task 10 effect observations). */
    get _inboundSequence(): bigint {
        return this.#state.inboundSequence;
    }

    /** Test-only read of the next outbound sequence. */
    get _outboundSequence(): bigint {
        return this.#state.outboundSequence;
    }

    /** Test-only read of the inbound sequence at the final teardown, before it is zeroed. */
    get _inboundSequenceAtTeardown(): bigint {
        return this.#inboundAtTeardown;
    }

    /** Test-only read of the outbound sequence at the final teardown, before it is zeroed. */
    get _outboundSequenceAtTeardown(): bigint {
        return this.#outboundAtTeardown;
    }

    async #onPhoneHello(state: EngineState, bytes: Uint8Array): Promise<SessionEffect[]> {
        const frame = this.#decodeFrame(bytes);
        if (frame === null || frame.type !== FrameType.RECONNECT_HELLO) {
            return this.#failPreAuth();
        }
        let hello: ReconnectHello;
        try {
            hello = decodeHello(frame.payload, Role.PHONE);
        } catch {
            return this.#failPreAuth();
        }
        // The selected peer, not any trusted peer: reject before signing.
        if (hexOf(hello.installationId) !== this.#deps.expectedPeerId) {
            return this.#failPreAuth();
        }
        let record: PhoneRecord | undefined;
        try {
            record = await this.#deps.lookupPhone(this.#deps.expectedPeerId);
        } catch {
            if (this.#state !== state) return [];
            return this.#terminate(STAGE_DEPENDENCY);
        }
        if (this.#state !== state) return [];
        const expired = this.#expiryFailure(this.#deps.now());
        if (expired !== null) return this.#terminate(expired);
        if (record === undefined) return this.#failPreAuth();
        if (state.phase !== 'awaiting_phone_hello' || state.clientHello === null) {
            return this.#terminate(STAGE_UNEXPECTED_FRAME);
        }
        state.peerId = this.#deps.expectedPeerId;
        state.peerRecord = {
            publicKey: record.publicKey.slice(),
            state: record.state,
            et: record.et?.slice(),
        };
        state.phoneHello = hello;
        state.transcript = reconnectRbase(frame.payload, state.clientHello);
        state.phase = 'awaiting_phone_signature';
        state.handshakeDeadlineAt = this.#deps.now() + HANDSHAKE_TIMEOUT_MS;
        return [];
    }

    async #onPhoneSignature(state: EngineState, bytes: Uint8Array): Promise<SessionEffect[]> {
        const frame = this.#decodeFrame(bytes);
        if (frame === null || frame.type !== FrameType.RECONNECT_SIGNATURE) {
            return this.#failPreAuth();
        }
        let signature: Uint8Array;
        try {
            signature = decodeSignatureFrame(frame.payload, Role.PHONE).signature;
        } catch {
            return this.#failPreAuth();
        }
        const peerKey = state.peerRecord?.publicKey;
        const transcript = state.transcript;
        const hello = state.phoneHello;
        const ephemeral = state.ephemeral;
        const localNonce = state.localNonce;
        if (
            peerKey === undefined ||
            transcript === null ||
            hello === null ||
            ephemeral === null ||
            localNonce === null
        ) {
            return this.#failPreAuth();
        }
        let valid = false;
        try {
            valid = await verifyP1363(peerKey, transcript, signature);
        } catch {
            valid = false;
        }
        if (this.#state !== state) return [];
        if (!valid) return this.#failPreAuth();

        let clientSignature: Uint8Array;
        try {
            clientSignature = await this.#deps.sign(transcript);
        } catch (error) {
            if (this.#state !== state) return [];
            if (error instanceof SessionReject) return this.#failPreAuth();
            throw error;
        }
        if (this.#state !== state) return [];
        if (clientSignature.length !== SIGNATURE_BYTES) return this.#failPreAuth();
        const expired = this.#expiryFailure(this.#deps.now());
        if (expired !== null) return this.#terminate(expired);

        let keys: ReconnectKeys | null = null;
        let input: Uint8Array;
        let confirmRecord: Uint8Array;
        let material: ReconnectKeyMaterial | null = null;
        try {
            const shared = await deriveSharedSecret(ephemeral.privateKey, hello.ephemeralPublicKey);
            try {
                material = await deriveKeyMaterial(
                    shared,
                    hello.nonce,
                    localNonce,
                    state.peerRecord?.et ?? null,
                );
                keys = await importKeys(material);
                // The AES keys are inside non-extractable CryptoKeys now; the
                // other arrays are owned by `keys` and must not be wiped here.
                material.c2pKey.fill(0);
                material.p2cKey.fill(0);
                material = null;
            } finally {
                shared.fill(0);
            }
            input = await confirmInput(transcript, signature, clientSignature);
            const ownState = isActiveFamily(state.peerRecord?.state ?? 'pending')
                ? PEER_STATE_ACTIVE
                : PEER_STATE_BELOW_ACTIVE;
            const body = concat(
                await confirmation(keys.clientConfirmKey, input),
                Uint8Array.of(ownState),
            );
            confirmRecord = await sealRecord(
                keys.c2pKey,
                keys.c2pPrefix,
                keys.sessionId.slice(0, SESSION_PREFIX_BYTES),
                Direction.CLIENT_TO_PHONE,
                RecordType.RECONNECT_CONFIRM,
                CONFIRM_SEQUENCE,
                body,
            );
        } catch {
            // Every failure path owns whatever has not been transferred.
            eraseKeyMaterial(material);
            material = null;
            eraseReconnectKeys(keys);
            keys = null;
            if (this.#state !== state) return [];
            return this.#failPreAuth();
        }
        const installed = keys;
        if (this.#state !== state) {
            eraseReconnectKeys(installed);
            return [];
        }
        const late = this.#expiryFailure(this.#deps.now());
        if (late !== null) {
            eraseReconnectKeys(installed);
            return this.#terminate(late);
        }
        if (installed === null) return this.#failPreAuth();
        const prefix = installed.sessionId.slice(0, SESSION_PREFIX_BYTES);
        const token = state.nextToken;
        state.keys = installed;
        state.sessionIdPrefix = prefix;
        state.confirmInput = input;
        state.phoneSignature = signature.slice();
        state.clientSignature = clientSignature.slice();
        state.confirmSendComplete = false;
        state.confirmToken = token;
        state.nextToken = token + 1;
        state.outboundSequence = CONFIRM_SEQUENCE + 1n;
        state.lastOutboundAt = this.#deps.now();
        state.phase = 'awaiting_phone_confirm';
        state.handshakeDeadlineAt = this.#deps.now() + HANDSHAKE_TIMEOUT_MS;
        return [
            {
                kind: 'send',
                bytes: encodeFrame(
                    FrameType.RECONNECT_SIGNATURE,
                    encodeSignatureFrame({ role: Role.CLIENT, signature: clientSignature }),
                ),
            },
            { kind: 'send', bytes: confirmRecord, completionToken: token },
        ];
    }

    async #onPhoneConfirm(state: EngineState, bytes: Uint8Array): Promise<SessionEffect[]> {
        if (this.#isPreAuthFrame(bytes)) return this.#terminate(STAGE_UNEXPECTED_FRAME);
        const keys = state.keys;
        const prefix = state.sessionIdPrefix;
        if (keys === null || prefix === null) return this.#terminate(STAGE_RECORD_INVALID);
        let opened: OpenedRecord;
        try {
            opened = await tryOpen(keys, prefix, state.inboundSequence, bytes);
        } catch {
            return this.#terminate(STAGE_RECORD_INVALID);
        }
        if (this.#state !== state) return [];
        // Expiry first: decryption is asynchronous and may outlive the wait.
        const openedAt = this.#expiryFailure(this.#deps.now());
        if (openedAt !== null) return this.#terminate(openedAt);
        state.lastActivityAt = this.#deps.now();
        if (opened.type !== RecordType.RECONNECT_CONFIRM) {
            return this.#terminate(STAGE_CONFIRM_REQUIRED);
        }
        let confirmed: number | null;
        try {
            confirmed = await this.#peerStateOf(state, opened.body);
        } catch {
            return this.#terminate(STAGE_CONFIRM_INVALID);
        }
        if (this.#state !== state) return [];
        const verifiedAt = this.#expiryFailure(this.#deps.now());
        if (verifiedAt !== null) return this.#terminate(verifiedAt);
        if (confirmed === null) return this.#terminate(STAGE_CONFIRM_INVALID);

        state.peerState = confirmed;
        state.peerConfirmVerified = true;
        state.inboundSequence = CONFIRM_SEQUENCE + 1n;
        // The handshake confirm is written once and never replaced. A second
        // protected record in the same wait state is rejected by the state
        // machine below, never treated as a fresh proof.
        if (state.verifiedPhoneConfirm === null) {
            state.verifiedPhoneConfirm = bytes.slice();
            state.phoneConfirmFinalizing = true;
        }

        // OTP2-SID-001: the mirror upgrade is evaluated BEFORE class selection.
        // A teardown after this call does not undo it.
        if (
            confirmed === PEER_STATE_ACTIVE &&
            state.peerRecord?.state === 'active_activation_pending' &&
            this.#deps.isCurrent()
        ) {
            const proof = bytes.slice();
            try {
                await this.#deps.upgradeMirror(state.peerId ?? this.#deps.expectedPeerId, proof);
            } catch {
                if (this.#state !== state) return [];
                return this.#terminate(STAGE_DEPENDENCY);
            }
            if (this.#state !== state) return [];
            // The mirror upgrade is a legitimate phone-proof write and stands;
            // an expired handshake must still not open the session.
            const upgradedAt = this.#expiryFailure(this.#deps.now());
            if (upgradedAt !== null) return this.#terminate(upgradedAt);
            if (state.peerRecord !== null) {
                state.peerRecord = { ...state.peerRecord, state: 'active' };
            }
        }
        if (!state.confirmSendComplete) return [];
        return this.#authenticate(state);
    }

    async #onProtectedRecord(state: EngineState, bytes: Uint8Array): Promise<SessionEffect[]> {
        if (this.#isPreAuthFrame(bytes)) return this.#terminate(STAGE_UNEXPECTED_FRAME);
        const keys = state.keys;
        const prefix = state.sessionIdPrefix;
        if (keys === null || prefix === null) return this.#terminate(STAGE_RECORD_INVALID);
        let opened: OpenedRecord;
        try {
            opened = await tryOpen(keys, prefix, state.inboundSequence, bytes);
        } catch {
            return this.#terminate(STAGE_RECORD_INVALID);
        }
        if (this.#state !== state) return [];
        // Expiry first: decryption is asynchronous and must not revive a
        // session that has already been idle for 180 s.
        const openedAt = this.#expiryFailure(this.#deps.now());
        if (openedAt !== null) return this.#terminate(openedAt);
        state.lastActivityAt = this.#deps.now();
        if (opened.seq === SEQUENCE_MAX) {
            state.inboundExhausted = true;
        } else {
            state.inboundSequence = opened.seq + 1n;
        }
        const effects = await this.#deliver(state, opened);
        if (this.#state !== state || state.phase !== 'open') return effects;

        // Chrome heartbeat driver: at most one, only while processing inbound,
        // and only after 60 s of outbound silence.
        const now = this.#deps.now();
        if (now - state.lastOutboundAt < HEARTBEAT_SILENCE_MS) return effects;
        let heartbeat: Uint8Array | null;
        try {
            heartbeat = await this.#sealApplication(
                state,
                RecordType.APP_HEARTBEAT,
                new Uint8Array(0),
            );
        } catch {
            if (this.#state !== state) return [];
            const expired = this.#expiryFailure(this.#deps.now());
            if (expired !== null) return this.#terminate(expired);
            return this.#terminate(STAGE_HEARTBEAT);
        }
        if (this.#state !== state) return [];
        if (heartbeat === null) return effects;
        return [...effects, { kind: 'send', bytes: heartbeat }];
    }

    async #deliver(
        state: EngineState,
        opened: { type: number; seq: bigint; body: Uint8Array },
    ): Promise<SessionEffect[]> {
        if (opened.type === RecordType.APP_ERROR) return this.#terminate(STAGE_APP_ERROR);
        if (opened.type === RecordType.APP_HEARTBEAT) {
            return this.#heartbeatReceived(state, this.#deps.now());
        }
        if (opened.type === RecordType.RECONNECT_CONFIRM) {
            return this.#terminate(STAGE_AUTHORIZATION);
        }
        // OTP2-REC-013 requester fencing: while a response is awaited only that response may
        // arrive (the allowed heartbeat and app_error returned above). Without the mark, a
        // response is a protocol violation. Either way the session ends and nothing is deleted.
        if (!acceptsDuringRemoval(state.removalPending, opened.type)) {
            return this.#terminate(STAGE_AUTHORIZATION);
        }
        const sessionClass = state.sessionClass;
        if (sessionClass === null) return this.#terminate(STAGE_AUTHORIZATION);
        if (!authorized(opened.type, Direction.PHONE_TO_CLIENT, sessionClass)) {
            return this.#terminate(STAGE_AUTHORIZATION);
        }
        const handler = state.handler;
        if (handler === null || !handler.handles(opened.type)) {
            return await this.#terminateUnhandled(state);
        }

        const replies: SessionEffect[] = [];
        let terminalError = false;
        // Replies are chained so a handler that does not await one still seals
        // in order, with each sequence reserved inside its own step. The chain
        // is drained before this event returns, so no encryption outlives it.
        let replyFailed = false;
        let replyFailure: unknown;
        let acceptingReplies = true;
        let replyChain: Promise<void> = Promise.resolve();
        const rejectLateReply = (): Promise<void> => {
            const rejected = Promise.reject(new SessionReject());
            // An ignored late call must not surface as an unhandled rejection;
            // an awaiting caller still observes it.
            void rejected.catch(() => undefined);
            return rejected;
        };
        const reply = (replyType: number, replyBody: Uint8Array): Promise<void> => {
            if (!acceptingReplies) return rejectLateReply();
            const task = replyChain.then(async () => {
                if (terminalError) return;
                const record = await this.#sealApplication(state, replyType, replyBody);
                if (record === null) return;
                replies.push({ kind: 'send', bytes: record });
                if (replyType === RecordType.APP_ERROR) terminalError = true;
            });
            const observed = task.catch((error: unknown) => {
                if (!replyFailed) {
                    replyFailed = true;
                    replyFailure = error;
                }
                throw error;
            });
            void observed.catch(() => undefined);
            replyChain = observed.catch(() => undefined);
            return observed;
        };

        let handlerFailed = false;
        let handlerFailure: unknown;
        let closeAfterReply = false;
        try {
            await handler.handle(opened.type, opened.body, reply);
            // Design D4: the handler must not tear the session down itself; it asks for
            // the reply-then-close outcome and the engine performs the terminal
            // transition after the buffered reply is sealed.
            closeAfterReply = handler.closeAfterReply?.(opened.type) === true;
        } catch (error) {
            handlerFailed = true;
            handlerFailure = error;
        }
        // Invalidate the callback, then drain every reply the handler issued,
        // awaited or not, so no encryption can commit after this event.
        acceptingReplies = false;
        await replyChain;
        if (this.#state !== state) {
            // An external teardown dropped this step's effects. A handler that completed and
            // asked for the reply-then-close outcome has already deleted its record on the
            // removal path, so the owner must still learn the terminal stage (design D4).
            if (!handlerFailed && closeAfterReply) {
                this.#droppedTerminalStage = terminalStage(terminalError, opened.type);
            }
            return [];
        }
        const expired = this.#expiryFailure(this.#deps.now());
        if (replyFailed || handlerFailed) {
            // A handler or reply that outlived the deadline ends through the deadline: a late
            // failure must never revive the session either.
            if (expired !== null) return this.#terminate(expired);
            if (terminalError) {
                this.#teardownInternal();
                return [
                    ...replies,
                    { kind: 'terminated', reason: STAGE_APP_ERROR },
                    { kind: 'close' },
                ];
            }
            const failure = replyFailed ? replyFailure : handlerFailure;
            return this.#terminate(
                failure instanceof SessionReject ? STAGE_AUTHORIZATION : STAGE_DEPENDENCY,
            );
        }
        if (terminalError || closeAfterReply) {
            // A completed handler outcome is decided before the deadline, as on the phone: a
            // handler that already deleted its record must report its terminal stage even when
            // its work outlived the idle deadline, or the owner would keep a record that trust no
            // longer holds. A failed or late reply above still ends through the deadline.
            this.#teardownInternal();
            return [
                ...replies,
                {
                    kind: 'terminated',
                    reason: terminalStage(terminalError, opened.type),
                    // A successful removal_request close is the only terminal effect whose
                    // reply the owner must still deliver: the responder's record is already
                    // deleted, so only the transport release may be deferred (design D4).
                    ...(!terminalError && opened.type === RecordType.REMOVAL_REQUEST
                        ? { holdForDelivery: true as const }
                        : {}),
                },
                { kind: 'close' },
            ];
        }
        if (expired !== null) return this.#terminate(expired);
        return [{ kind: 'deliver', type: opened.type, body: opened.body }, ...replies];
    }

    /** §11.4 unhandled authorized record: a sealed `app_error`, then close. */
    async #terminateUnhandled(state: EngineState): Promise<SessionEffect[]> {
        if (this.#state !== state) return [];
        const expired = this.#expiryFailure(this.#deps.now());
        if (expired !== null) return this.#terminate(expired);
        let error: Uint8Array | null;
        try {
            error = await this.#sealApplication(
                state,
                RecordType.APP_ERROR,
                appErrorBody(AppErrorCode.PROTOCOL_VIOLATION),
            );
        } catch {
            if (this.#state !== state) return [];
            const lateExpiry = this.#expiryFailure(this.#deps.now());
            if (lateExpiry !== null) return this.#terminate(lateExpiry);
            error = null;
        }
        if (this.#state !== state) return [];
        if (error === null) return this.#terminate(STAGE_UNHANDLED);
        this.#teardownInternal();
        return [
            { kind: 'send', bytes: error },
            { kind: 'terminated', reason: STAGE_UNHANDLED },
            { kind: 'close' },
        ];
    }

    /** OTP2-REC-034: the fifth heartbeat in a rolling 60 s window ends the session. */
    #heartbeatReceived(state: EngineState, now: number): SessionEffect[] {
        const times = state.receivedHeartbeats;
        while (times.length > 0 && now - times[0] >= HEARTBEAT_WINDOW_MS) times.shift();
        if (times.length >= HEARTBEAT_TERMINATE_THRESHOLD) {
            return this.#terminate(STAGE_HEARTBEAT);
        }
        times.push(now);
        return [];
    }

    /**
     * Seals one outbound application record. The single heartbeat-spacing gate
     * lives here so every path — direct sends, ticks and handler replies —
     * ignores an early heartbeat without reserving a sequence. A null result
     * means "nothing was sent".
     */
    async #sealApplication(
        state: EngineState,
        type: number,
        body: Uint8Array,
    ): Promise<Uint8Array | null> {
        const plan = this.#planSeal(state, type, body);
        if (plan === null) return null;
        let record: Uint8Array;
        try {
            record = await sealRecord(
                plan.keys.c2pKey,
                plan.keys.c2pPrefix,
                plan.prefix,
                Direction.CLIENT_TO_PHONE,
                type,
                plan.sequence,
                body,
            );
        } catch {
            // The sequence and nonce are already reserved; a rejected seal must
            // end the session rather than continue with a gap.
            throw new SealFailure();
        }
        if (this.#state !== state) throw new SealFailure();
        // Expiry first: encryption is asynchronous, so the session may have
        // expired while the record was being sealed. Never let a late seal
        // refresh activity.
        if (this.#expiryFailure(this.#deps.now()) !== null) throw new SealFailure();
        this.#commitSeal(state, plan.now);
        return record;
    }

    #planSeal(
        state: EngineState,
        type: number,
        body: Uint8Array,
    ): {
        keys: ReconnectKeys;
        prefix: Uint8Array;
        sequence: bigint;
        now: number;
    } | null {
        if (this.#state !== state || state.phase === 'closed') reject();
        const now = this.#deps.now();
        // Expiry first: a reply that arrives after the deadline must not revive
        // the session by refreshing its activity.
        if (this.#expiryFailure(now) !== null) reject();
        if (state.outboundExhausted) reject();
        if (type === RecordType.RECONNECT_CONFIRM) reject();
        const sessionClass = state.sessionClass;
        if (sessionClass === null) reject();
        if (!authorized(type, Direction.CLIENT_TO_PHONE, sessionClass)) reject();
        if (type === RecordType.APP_HEARTBEAT && this.#heartbeatSendTooEarly(state, now)) {
            return null;
        }
        const keys = state.keys;
        const prefix = state.sessionIdPrefix;
        if (keys === null || prefix === null) reject();
        // Reject data that `sealRecord` would refuse BEFORE reserving anything,
        // so an invalid direct send cannot consume a sequence.
        if (body.length > RECORD_MAX_CIPHERTEXT_BYTES) reject();
        // Reserve the sequence — and so the nonce — before the asynchronous
        // sealing step. Concurrent work can never share or rewind a sequence.
        const sequence = state.outboundSequence;
        if (sequence === SEQUENCE_MAX) {
            state.outboundExhausted = true;
        } else {
            state.outboundSequence = sequence + 1n;
        }
        if (type === RecordType.APP_HEARTBEAT) state.lastHeartbeatSentAt = now;
        return { keys, prefix, sequence, now };
    }

    /**
     * Records the timing of a completed seal. `Math.max` keeps a late
     * completion from rewinding a clock that later seals already advanced.
     */
    #commitSeal(state: EngineState, now: number): void {
        state.lastOutboundAt = Math.max(state.lastOutboundAt, now);
        if (state.phase === 'open') {
            state.lastActivityAt = Math.max(state.lastActivityAt, now);
        }
    }

    #heartbeatSendTooEarly(state: EngineState, now: number): boolean {
        const last = state.lastHeartbeatSentAt;
        if (last === null) return false;
        return now - last < HEARTBEAT_MIN_INTERVAL_MS;
    }

    async #peerStateOf(state: EngineState, body: Uint8Array): Promise<number | null> {
        if (body.length !== CONFIRM_BODY_BYTES) return null;
        const keys = state.keys;
        const input = state.confirmInput;
        if (keys === null || input === null) return null;
        const expected = await confirmation(keys.phoneConfirmKey, input);
        if (!equalConstantTime(expected, body.subarray(0, HASH_BYTES))) return null;
        const value = body[HASH_BYTES];
        return value === PEER_STATE_ACTIVE || value === PEER_STATE_BELOW_ACTIVE ? value : null;
    }

    async #authenticate(state: EngineState): Promise<SessionEffect[]> {
        if (this.#state !== state || state.phase === 'closed') return [];
        const expired = this.#expiryFailure(this.#deps.now());
        if (expired !== null) return this.#terminate(expired);
        if (state.authenticated) return [];
        const record = state.peerRecord;
        const peerId = state.peerId;
        const prefix = state.sessionIdPrefix;
        if (record === null || peerId === null || prefix === null) {
            return this.#terminate(STAGE_RECORD_INVALID);
        }
        const sessionClass = selectSessionClass(record.state, state.peerState);
        // OTP2-CRY-019f: the class is only fixed here, so a `full` session zeroes the recovery
        // keys derived at the handshake. Every other class keeps them for its activation rows.
        if (sessionClass === SessionClass.FULL) eraseRecoveryKeys(state.keys?.recovery ?? null);
        state.sessionClass = sessionClass;
        state.phoneConfirmFinalizing = false;
        state.authenticated = true;
        state.phase = 'open';
        state.lastActivityAt = this.#deps.now();
        // OTP-10 plan 9b: the handler gets the fixed session facts once, here, before the
        // authenticated effect and any inbound record. A full class hands out no recovery keys.
        const handler = this.#deps.handler;
        state.handler =
            handler === undefined
                ? null
                : (handler.forSession?.({
                      sessionIdPrefix: prefix.slice(),
                      sessionClass,
                      peerId,
                      et: record.et?.slice() ?? null,
                      peerState: state.peerState,
                      recovery:
                          sessionClass === SessionClass.FULL
                              ? null
                              : (state.keys?.recovery ?? null),
                  }) ?? handler);
        // OTP2-STA-010b steps 0/5: the context is fixed here, but the owner sends these after
        // the effect through `sendApplication`, never from inside `forSession`.
        let unprompted: UnpromptedApplication[] = [];
        try {
            unprompted = (await state.handler?.unprompted?.()) ?? [];
        } catch {
            if (this.#state !== state) return [];
            return this.#terminate(STAGE_DEPENDENCY);
        }
        return [
            {
                kind: 'authenticated',
                sessionClass,
                peerId,
                sessionIdPrefix: prefix.slice(),
                ...(unprompted.length === 0 ? {} : { unprompted }),
            },
        ];
    }

    #expiryFailure(now: number): string | null {
        const state = this.#state;
        switch (state.phase) {
            case 'awaiting_phone_hello':
            case 'awaiting_phone_signature':
            case 'awaiting_phone_confirm':
                return now >= state.handshakeDeadlineAt ? STAGE_HANDSHAKE_TIMEOUT : null;
            case 'open':
                return now - state.lastActivityAt >= IDLE_TIMEOUT_MS ? STAGE_IDLE : null;
            default:
                return null;
        }
    }

    #exhausted(state: EngineState): boolean {
        return state.inboundExhausted || state.outboundExhausted;
    }

    #decodeFrame(bytes: Uint8Array): { type: number; payload: Uint8Array } | null {
        try {
            return decodeFrame(bytes);
        } catch {
            return null;
        }
    }

    #isHelloFrame(bytes: Uint8Array): boolean {
        return bytes.length > 0 && bytes[0] === FrameType.RECONNECT_HELLO;
    }

    #isPreAuthFrame(bytes: Uint8Array): boolean {
        return bytes.length > 0 && frameTypeFromWire(bytes[0]) !== null;
    }

    /**
     * The one generic pre-auth client failure (OTP2-REV-001). `preauth_reject` is
     * phone→client only (OTP2-ENC-010, §4), so the client sends no frame: it closes
     * with the same reason-free failure it would show for any rejected input.
     */
    #failPreAuth(): SessionEffect[] {
        this.#teardownInternal();
        return [{ kind: 'close' }];
    }

    #terminate(reason: string): SessionEffect[] {
        this.#teardownInternal();
        return [{ kind: 'terminated', reason }, { kind: 'close' }];
    }

    /**
     * Idempotent final transition. A teardown from outside may reach this while
     * an operation is suspended; the state replacement makes the late result
     * observe a different object and install nothing.
     */
    #teardownInternal(): void {
        const state = this.#state;
        if (state.phase === 'closed') return;
        this.#inboundAtTeardown = state.inboundSequence;
        this.#outboundAtTeardown = state.outboundSequence;
        eraseState(state);
        this.#state = freshState('closed');
    }
}

function appErrorBody(code: AppErrorCode): Uint8Array {
    return Uint8Array.of(code, 0, 0);
}

/** OTP2-STA-015: zero every engine-owned byte array, then drop every reference. */
function eraseState(state: EngineState): void {
    eraseReconnectKeys(state.keys);
    // A `CryptoKey` exposes no zeroable scalar; dropping the reference is the
    // erasure the platform allows. Every engine-owned byte array is zeroed.
    state.ephemeral = null;
    state.localNonce?.fill(0);
    state.localNonce = null;
    state.clientHello = null;
    state.phoneHello = null;
    state.transcript?.fill(0);
    state.transcript = null;
    state.phoneSignature?.fill(0);
    state.phoneSignature = null;
    state.clientSignature?.fill(0);
    state.clientSignature = null;
    state.keys = null;
    state.sessionIdPrefix?.fill(0);
    state.sessionIdPrefix = null;
    state.sessionClass = null;
    state.confirmInput?.fill(0);
    state.confirmInput = null;
    state.peerRecord = null;
    state.peerId = null;
    state.confirmToken = null;
    state.confirmSendComplete = false;
    state.peerConfirmVerified = false;
    state.verifiedPhoneConfirm?.fill(0);
    state.verifiedPhoneConfirm = null;
    state.phoneConfirmFinalizing = false;
    state.authenticated = false;
    state.handler = null;
    state.peerState = 0;
    state.nextToken = 0;
    state.outboundSequence = 0n;
    state.inboundSequence = 0n;
    state.outboundExhausted = false;
    state.inboundExhausted = false;
    state.lastActivityAt = 0;
    state.lastOutboundAt = 0;
    state.lastHeartbeatSentAt = null;
    state.receivedHeartbeats.length = 0;
    state.handshakeDeadlineAt = 0;
    state.phase = 'closed';
}
