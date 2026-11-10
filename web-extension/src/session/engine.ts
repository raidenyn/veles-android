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
import { openRecord, sealRecord, type OpenedRecord } from './record';
import { SessionReject } from './reject';
import {
    confirmation,
    confirmInput,
    deriveKeyMaterial,
    importKeys,
    rbase as reconnectRbase,
    type ReconnectKeys,
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
const PEER_STATE_ACTIVE = 0x01;
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

/** Application records that have a handler. An authorized record without one fails closed. */
export interface ApplicationHandler {
    handles(type: number): boolean;
    handle(
        type: number,
        body: Uint8Array,
        reply: (type: number, body: Uint8Array) => Promise<void>,
    ): Promise<void>;
}

/** Effects that a session host turns into transport writes, host callbacks and teardown. */
export type SessionEffect =
    | { kind: 'send'; bytes: Uint8Array; completionToken?: number }
    | {
          kind: 'authenticated';
          sessionClass: SessionClass;
          peerId: string;
          sessionIdPrefix: Uint8Array;
      }
    | { kind: 'deliver'; type: number; body: Uint8Array }
    | { kind: 'terminated'; reason: string }
    | { kind: 'close' };

/** Everything the client engine reads or writes outside its own state. */
export interface ClientEngineDeps {
    localInstallationId: Uint8Array;
    sign(rbase: Uint8Array): Promise<Uint8Array>;
    /** Always set: the intended trust-record id. A phone hello naming any other id fails before signing. */
    expectedPeerId: string;
    /** Generation fence, checked immediately before `upgradeMirror`. */
    isCurrent(): boolean;
    lookupPhone(
        id: string,
    ): Promise<{ publicKey: Uint8Array; state: PhoneRecordState } | undefined>;
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
    authenticated: boolean;
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
        authenticated: false,
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

function equalConstantTime(left: Uint8Array, right: Uint8Array): boolean {
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

    constructor(deps: ClientEngineDeps) {
        this.#deps = deps;
        this.#localInstallationId = deps.localInstallationId.slice();
    }

    get isOpen(): boolean {
        return this.#state.phase === 'open';
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
            return this.#rejectPreAuth();
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
        const record = await this.#sealApplication(state, type, body);
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

    async #onPhoneHello(state: EngineState, bytes: Uint8Array): Promise<SessionEffect[]> {
        const frame = this.#decodeFrame(bytes);
        if (frame === null || frame.type !== FrameType.RECONNECT_HELLO) {
            return this.#rejectPreAuth();
        }
        let hello: ReconnectHello;
        try {
            hello = decodeHello(frame.payload, Role.PHONE);
        } catch {
            return this.#rejectPreAuth();
        }
        // The selected peer, not any trusted peer: reject before signing.
        if (hexOf(hello.installationId) !== this.#deps.expectedPeerId) {
            return this.#rejectPreAuth();
        }
        let record: { publicKey: Uint8Array; state: PhoneRecordState } | undefined;
        try {
            record = await this.#deps.lookupPhone(this.#deps.expectedPeerId);
        } catch {
            if (this.#state !== state) return [];
            return this.#terminate(STAGE_DEPENDENCY);
        }
        if (this.#state !== state) return [];
        const expired = this.#expiryFailure(this.#deps.now());
        if (expired !== null) return this.#terminate(expired);
        if (record === undefined) return this.#rejectPreAuth();
        if (state.phase !== 'awaiting_phone_hello' || state.clientHello === null) {
            return this.#terminate(STAGE_UNEXPECTED_FRAME);
        }
        state.peerId = this.#deps.expectedPeerId;
        state.peerRecord = { publicKey: record.publicKey.slice(), state: record.state };
        state.phoneHello = hello;
        state.transcript = reconnectRbase(frame.payload, state.clientHello);
        state.phase = 'awaiting_phone_signature';
        state.handshakeDeadlineAt = this.#deps.now() + HANDSHAKE_TIMEOUT_MS;
        return [];
    }

    async #onPhoneSignature(state: EngineState, bytes: Uint8Array): Promise<SessionEffect[]> {
        const frame = this.#decodeFrame(bytes);
        if (frame === null || frame.type !== FrameType.RECONNECT_SIGNATURE) {
            return this.#rejectPreAuth();
        }
        let signature: Uint8Array;
        try {
            signature = decodeSignatureFrame(frame.payload, Role.PHONE).signature;
        } catch {
            return this.#rejectPreAuth();
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
            return this.#rejectPreAuth();
        }
        let valid = false;
        try {
            valid = await verifyP1363(peerKey, transcript, signature);
        } catch {
            valid = false;
        }
        if (this.#state !== state) return [];
        if (!valid) return this.#rejectPreAuth();

        let clientSignature: Uint8Array;
        try {
            clientSignature = await this.#deps.sign(transcript);
        } catch (error) {
            if (this.#state !== state) return [];
            if (error instanceof SessionReject) return this.#rejectPreAuth();
            throw error;
        }
        if (this.#state !== state) return [];
        if (clientSignature.length !== SIGNATURE_BYTES) return this.#rejectPreAuth();
        const expired = this.#expiryFailure(this.#deps.now());
        if (expired !== null) return this.#terminate(expired);

        let keys: ReconnectKeys;
        let input: Uint8Array;
        let confirmRecord: Uint8Array;
        try {
            const shared = await deriveSharedSecret(ephemeral.privateKey, hello.ephemeralPublicKey);
            try {
                const material = await deriveKeyMaterial(shared, hello.nonce, localNonce);
                keys = await importKeys(material);
                material.c2pKey.fill(0);
                material.p2cKey.fill(0);
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
            if (this.#state !== state) return [];
            return this.#rejectPreAuth();
        }
        if (this.#state !== state) return [];
        const late = this.#expiryFailure(this.#deps.now());
        if (late !== null) return this.#terminate(late);
        const prefix = keys.sessionId.slice(0, SESSION_PREFIX_BYTES);
        const token = state.nextToken;
        state.keys = keys;
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
        if (confirmed === null) return this.#terminate(STAGE_CONFIRM_INVALID);

        state.peerState = confirmed;
        state.peerConfirmVerified = true;
        state.inboundSequence = CONFIRM_SEQUENCE + 1n;

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
        const sessionClass = state.sessionClass;
        if (sessionClass === null) return this.#terminate(STAGE_AUTHORIZATION);
        if (!authorized(opened.type, Direction.PHONE_TO_CLIENT, sessionClass)) {
            return this.#terminate(STAGE_AUTHORIZATION);
        }
        const handler = this.#deps.handler;
        if (handler === undefined || !handler.handles(opened.type)) {
            return await this.#terminateUnhandled(state);
        }

        const replies: SessionEffect[] = [];
        let terminalError = false;
        // Replies are chained so a handler that does not await one still
        // seals in order, with each sequence reserved inside its own step.
        let replyChain: Promise<void> = Promise.resolve();
        const reply = (replyType: number, replyBody: Uint8Array): Promise<void> => {
            const next = replyChain.then(async () => {
                if (terminalError) return;
                const record = await this.#sealApplication(state, replyType, replyBody);
                if (record === null) return;
                replies.push({ kind: 'send', bytes: record });
                if (replyType === RecordType.APP_ERROR) terminalError = true;
            });
            replyChain = next.catch(() => undefined);
            return next;
        };
        try {
            await handler.handle(opened.type, opened.body, reply);
        } catch (error) {
            if (this.#state !== state) return [];
            if (!terminalError) {
                return this.#terminate(
                    error instanceof SessionReject ? STAGE_AUTHORIZATION : STAGE_DEPENDENCY,
                );
            }
            this.#teardownInternal();
            return [...replies, { kind: 'terminated', reason: STAGE_APP_ERROR }, { kind: 'close' }];
        }
        if (this.#state !== state) return [];
        if (terminalError) {
            this.#teardownInternal();
            return [...replies, { kind: 'terminated', reason: STAGE_APP_ERROR }, { kind: 'close' }];
        }
        const expired = this.#expiryFailure(this.#deps.now());
        if (expired !== null) return this.#terminate(expired);
        return [{ kind: 'deliver', type: opened.type, body: opened.body }, ...replies];
    }

    /** §11.4 unhandled authorized record: a sealed `app_error`, then close. */
    async #terminateUnhandled(state: EngineState): Promise<SessionEffect[]> {
        let error: Uint8Array | null;
        try {
            error = await this.#sealApplication(
                state,
                RecordType.APP_ERROR,
                appErrorBody(AppErrorCode.PROTOCOL_VIOLATION),
            );
        } catch {
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
        const plan = this.#planSeal(state, type);
        if (plan === null) return null;
        const record = await sealRecord(
            plan.keys.c2pKey,
            plan.keys.c2pPrefix,
            plan.prefix,
            Direction.CLIENT_TO_PHONE,
            type,
            plan.sequence,
            body,
        );
        if (this.#state !== state) reject();
        this.#commitSeal(state, type, plan.sequence, plan.now);
        return record;
    }

    #planSeal(
        state: EngineState,
        type: number,
    ): {
        keys: ReconnectKeys;
        prefix: Uint8Array;
        sequence: bigint;
        now: number;
    } | null {
        if (this.#state !== state || state.phase === 'closed') reject();
        if (state.outboundExhausted) reject();
        if (type === RecordType.RECONNECT_CONFIRM) reject();
        const sessionClass = state.sessionClass;
        if (sessionClass === null) reject();
        if (!authorized(type, Direction.CLIENT_TO_PHONE, sessionClass)) reject();
        const now = this.#deps.now();
        if (type === RecordType.APP_HEARTBEAT && this.#heartbeatSendTooEarly(state, now)) {
            return null;
        }
        const keys = state.keys;
        const prefix = state.sessionIdPrefix;
        if (keys === null || prefix === null) reject();
        return { keys, prefix, sequence: state.outboundSequence, now };
    }

    #commitSeal(state: EngineState, type: number, sequence: bigint, now: number): void {
        if (sequence === SEQUENCE_MAX) {
            state.outboundExhausted = true;
        } else {
            state.outboundSequence = sequence + 1n;
        }
        state.lastOutboundAt = now;
        if (type === RecordType.APP_HEARTBEAT) state.lastHeartbeatSentAt = now;
        if (state.phase === 'open') state.lastActivityAt = now;
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

    #authenticate(state: EngineState): SessionEffect[] {
        if (this.#state !== state || state.phase === 'closed') return [];
        if (state.authenticated) return [];
        const record = state.peerRecord;
        const peerId = state.peerId;
        const prefix = state.sessionIdPrefix;
        if (record === null || peerId === null || prefix === null) {
            return this.#terminate(STAGE_RECORD_INVALID);
        }
        const sessionClass = selectSessionClass(record.state, state.peerState);
        state.sessionClass = sessionClass;
        state.authenticated = true;
        state.phase = 'open';
        state.lastActivityAt = this.#deps.now();
        return [
            {
                kind: 'authenticated',
                sessionClass,
                peerId,
                sessionIdPrefix: prefix.slice(),
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

    #rejectPreAuth(): SessionEffect[] {
        const frame = encodeFrame(FrameType.PREAUTH_REJECT, new Uint8Array(0));
        this.#teardownInternal();
        return [{ kind: 'send', bytes: frame }, { kind: 'close' }];
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
        eraseState(state);
        this.#state = freshState('closed');
    }
}

function appErrorBody(code: AppErrorCode): Uint8Array {
    return Uint8Array.of(code, 0, 0);
}

/** OTP2-STA-015: zero every engine-owned byte array, then drop every reference. */
function eraseState(state: EngineState): void {
    const keys = state.keys;
    if (keys !== null) {
        keys.c2pPrefix.fill(0);
        keys.clientConfirmKey.fill(0);
        keys.p2cPrefix.fill(0);
        keys.phoneConfirmKey.fill(0);
        keys.sessionId.fill(0);
    }
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
    state.authenticated = false;
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
