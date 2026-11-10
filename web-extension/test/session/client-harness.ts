import {
    decodeFrame,
    decodeSignatureFrame,
    encodeFrame,
    encodeSignatureFrame,
} from '../../src/session/frames';
import { normalizeLowS } from '../../src/session/ecdsa';
import { LOCAL_CAPS, decodeHello, encodeHello, type ReconnectHello } from '../../src/session/hello';
import { requireValidPoint } from '../../src/session/p256';
import {
    RECORD_HEADER_BYTES,
    openRecord,
    sealRecord,
    type OpenedRecord,
} from '../../src/session/record';
import {
    confirmation,
    confirmInput,
    deriveKeyMaterial,
    importKeys,
    rbase as reconnectRbase,
    type ReconnectKeys,
} from '../../src/session/schedule';
import { Direction, FrameType, RecordType, Role } from '../../src/session/wire';
import {
    ClientSessionEngine,
    type ApplicationHandler,
    type ClientEngineDeps,
    type SessionEffect,
} from '../../src/session/engine';
import type { TrustState } from '../../src/trust/model';

// Test-only scripted phone. It drives the production client engine through the
// §11.3 exchange and plays the phone half with the production codecs, mirroring
// the Kotlin `TestClient`/`TestPeers` harness.

export const PEER_STATE_ACTIVE = 0x01;
export const PEER_STATE_BELOW_ACTIVE = 0x02;
export const SESSION_PREFIX_BYTES = 8;

const CLIENT_INSTALLATION_ID = Uint8Array.from({ length: 16 }, (_, index) => 0xa0 + index);
const NONCE_BYTES = 16;
const TAG_BYTES = 16;
const GCM_TAG_BITS = 128;
const BITS_PER_BYTE = 8;
const U8_MASK = 0xff;
const SEQUENCE_OFFSET = 5;
const LENGTH_OFFSET = 13;
const SESSION_OFFSET = 15;
const HEADER_VERSION = 1;

export function hexOf(value: Uint8Array): string {
    return Array.from(value, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

type SubtleMethod = (...args: unknown[]) => Promise<unknown>;

/** Captures a bound `crypto.subtle` method so a test can delegate to it. */
export function takeSubtle(name: 'encrypt' | 'decrypt' | 'importKey' | 'verify'): SubtleMethod {
    return (crypto.subtle[name] as unknown as SubtleMethod).bind(crypto.subtle);
}

/** Replaces one `crypto.subtle` method; the returned function restores it. */
export function patchSubtle(
    name: 'encrypt' | 'decrypt' | 'importKey' | 'verify',
    replacement: SubtleMethod,
): () => void {
    Object.defineProperty(crypto.subtle, name, { value: replacement, configurable: true });
    return () => {
        Reflect.deleteProperty(crypto.subtle, name);
    };
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

function writeU64(value: Uint8Array, offset: number, sequence: bigint): void {
    let remaining = sequence;
    for (let index = offset + 7; index >= offset; index -= 1) {
        value[index] = Number(remaining & 0xffn);
        remaining >>= 8n;
    }
}

function appErrorBody(code: number, reason: Uint8Array = new Uint8Array(0)): Uint8Array {
    const body = new Uint8Array(3 + reason.length);
    body[0] = code;
    body[1] = (reason.length >>> BITS_PER_BYTE) & U8_MASK;
    body[2] = reason.length & U8_MASK;
    body.set(reason, 3);
    return body;
}

export { appErrorBody };

/** Injected monotonic clock, mirroring the Kotlin `FakeClock`. */
export class FakeClock {
    value = 1_000;

    now = (): number => this.value;

    advance(millis: number): void {
        this.value += millis;
    }
}

export interface TestPhoneOptions {
    identity?: CryptoKeyPair;
    ephemeral?: CryptoKeyPair;
    nonce?: Uint8Array;
    installationId?: Uint8Array;
}

/** A software P-256 phone identity plus its per-connection ephemeral key and nonce. */
export class TestPhone {
    private constructor(
        readonly identity: CryptoKeyPair,
        readonly ephemeral: CryptoKeyPair,
        readonly nonce: Uint8Array,
        readonly installationId: Uint8Array,
        readonly publicKey: Uint8Array,
        readonly ephemeralPublicKey: Uint8Array,
    ) {}

    static async create(options: TestPhoneOptions = {}): Promise<TestPhone> {
        const identity =
            options.identity ??
            (await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, true, [
                'sign',
                'verify',
            ]));
        const ephemeral =
            options.ephemeral ??
            (await crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, true, [
                'deriveBits',
            ]));
        const publicKey = new Uint8Array(await crypto.subtle.exportKey('raw', identity.publicKey));
        return new TestPhone(
            identity,
            ephemeral,
            options.nonce ?? randomBytes(NONCE_BYTES),
            // A stable id per identity: reusing an identity reuses its id.
            options.installationId ?? publicKey.slice(0, 16),
            publicKey,
            new Uint8Array(await crypto.subtle.exportKey('raw', ephemeral.publicKey)),
        );
    }

    helloPayload(
        overrides: {
            installationId?: Uint8Array;
            ephemeralPublicKey?: Uint8Array;
            nonce?: Uint8Array;
        } = {},
    ): Uint8Array {
        return encodeHello({
            installationId: overrides.installationId ?? this.installationId,
            ephemeralPublicKey: overrides.ephemeralPublicKey ?? this.ephemeralPublicKey,
            nonce: overrides.nonce ?? this.nonce,
            role: Role.PHONE,
            caps: LOCAL_CAPS.slice(),
        });
    }

    async sign(message: Uint8Array): Promise<Uint8Array> {
        return normalizeLowS(
            new Uint8Array(
                await crypto.subtle.sign(
                    { name: 'ECDSA', hash: 'SHA-256' },
                    this.identity.privateKey,
                    message,
                ),
            ),
        );
    }
}

function randomBytes(length: number): Uint8Array {
    return crypto.getRandomValues(new Uint8Array(length));
}

export interface PhoneRecord {
    publicKey: Uint8Array;
    state: TrustState;
}

export interface PhoneHarnessOptions {
    /** Local client trust-record state used to seed `store` when the record is absent. */
    state?: TrustState;
    phoneId?: string;
    phoneIdentity?: CryptoKeyPair;
    phoneEphemeral?: CryptoKeyPair;
    phoneNonce?: Uint8Array;
    clientEphemeral?: () => Promise<CryptoKeyPair>;
    clientNonce?: () => Uint8Array;
    expectedPeerId?: string;
    store?: Map<string, PhoneRecord>;
    clock?: FakeClock;
    isCurrent?: () => boolean;
    handler?: ApplicationHandler;
    sign?: (message: Uint8Array) => Promise<Uint8Array>;
    lookup?: (id: string) => Promise<PhoneRecord | undefined>;
    upgrade?: (peerId: string, proof: Uint8Array) => Promise<void>;
}

export interface HarnessCalls {
    sign: number;
    lookup: number;
    upgrade: { peerId: string; proof: Uint8Array }[];
    isCurrent: number;
}

export interface StartedHandshake {
    effects: SessionEffect[];
    helloFrame: Uint8Array;
    clientHelloPayload: Uint8Array;
    clientHello: ReconnectHello;
}

export interface HandshakeDrive {
    started: StartedHandshake;
    phoneHelloPayload: Uint8Array;
    phoneSignature: Uint8Array;
    clientSignature: Uint8Array;
    clientConfirmRecord: Uint8Array;
    clientConfirm: OpenedRecord;
    transcript: Uint8Array;
    confirmInput: Uint8Array;
    token: number;
}

export function sendBytes(effects: SessionEffect[], index = 0): Uint8Array {
    const effect = effects[index];
    if (effect === undefined || effect.kind !== 'send') {
        throw new Error(`expected a send effect at ${index}`);
    }
    return effect.bytes;
}

export function completionToken(effects: SessionEffect[], index = 0): number {
    const effect = effects[index];
    if (effect === undefined || effect.kind !== 'send' || effect.completionToken === undefined) {
        throw new Error(`expected a completion token at ${index}`);
    }
    return effect.completionToken;
}

/** Asserts the canonical `[terminated, close]` pair and returns the bounded reason. */
export function terminatedReason(effects: SessionEffect[]): string {
    const terminated = effects[0];
    const close = effects[1];
    if (effects.length !== 2 || terminated?.kind !== 'terminated' || close?.kind !== 'close') {
        throw new Error(`expected [terminated, close], got ${effects.length} effects`);
    }
    return terminated.reason;
}

export class PhoneHarness {
    readonly phone: TestPhone;
    readonly clientIdentity: CryptoKeyPair;
    readonly clientPublicKey: Uint8Array;
    readonly localInstallationId = CLIENT_INSTALLATION_ID.slice();
    readonly store: Map<string, PhoneRecord>;
    readonly clock: FakeClock;
    readonly calls: HarnessCalls;
    readonly peerId: string;
    readonly engine: ClientSessionEngine;

    keys: ReconnectKeys | null = null;
    transcript: Uint8Array = new Uint8Array(0);
    phoneSignature: Uint8Array = new Uint8Array(0);
    confirmInput: Uint8Array = new Uint8Array(0);

    private constructor(
        phone: TestPhone,
        clientIdentity: CryptoKeyPair,
        clientPublicKey: Uint8Array,
        store: Map<string, PhoneRecord>,
        clock: FakeClock,
        calls: HarnessCalls,
        peerId: string,
        deps: ClientEngineDeps,
    ) {
        this.phone = phone;
        this.clientIdentity = clientIdentity;
        this.clientPublicKey = clientPublicKey;
        this.store = store;
        this.clock = clock;
        this.calls = calls;
        this.peerId = peerId;
        this.engine = new ClientSessionEngine(deps);
    }

    static async create(options: PhoneHarnessOptions = {}): Promise<PhoneHarness> {
        const phone = await TestPhone.create({
            identity: options.phoneIdentity,
            ephemeral: options.phoneEphemeral,
            nonce: options.phoneNonce,
        });
        const peerId = options.phoneId ?? hexOf(phone.installationId);
        const store = options.store ?? new Map<string, PhoneRecord>();
        if (!store.has(peerId)) {
            store.set(peerId, { publicKey: phone.publicKey, state: options.state ?? 'active' });
        }
        const clientIdentity = await crypto.subtle.generateKey(
            { name: 'ECDSA', namedCurve: 'P-256' },
            true,
            ['sign', 'verify'],
        );
        const clientPublicKey = new Uint8Array(
            await crypto.subtle.exportKey('raw', clientIdentity.publicKey),
        );
        const clock = options.clock ?? new FakeClock();
        const expectedPeerId = options.expectedPeerId ?? peerId;
        const calls: HarnessCalls = { sign: 0, lookup: 0, upgrade: [], isCurrent: 0 };

        const lookup = options.lookup;
        const upgrade = options.upgrade;
        const sign = options.sign;
        const isCurrent = options.isCurrent;
        const deps: ClientEngineDeps = {
            localInstallationId: CLIENT_INSTALLATION_ID.slice(),
            sign: async (message) => {
                calls.sign += 1;
                if (sign !== undefined) return sign(message);
                return normalizeLowS(
                    new Uint8Array(
                        await crypto.subtle.sign(
                            { name: 'ECDSA', hash: 'SHA-256' },
                            clientIdentity.privateKey,
                            message,
                        ),
                    ),
                );
            },
            expectedPeerId,
            isCurrent: () => {
                calls.isCurrent += 1;
                return isCurrent?.() ?? true;
            },
            lookupPhone: async (id) => {
                calls.lookup += 1;
                if (lookup !== undefined) return lookup(id);
                return store.get(id);
            },
            upgradeMirror: async (peerIdValue, proof) => {
                calls.upgrade.push({ peerId: peerIdValue, proof: proof.slice() });
                if (upgrade !== undefined) {
                    await upgrade(peerIdValue, proof);
                    return;
                }
                const record = store.get(peerIdValue);
                if (record !== undefined) record.state = 'active';
            },
            now: clock.now,
            ephemeral: options.clientEphemeral,
            nonce: options.clientNonce,
            handler: options.handler,
        };
        return new PhoneHarness(
            phone,
            clientIdentity,
            clientPublicKey,
            store,
            clock,
            calls,
            peerId,
            deps,
        );
    }

    get prefix(): Uint8Array {
        const keys = this.keys;
        if (keys === null) throw new Error('keys are not derived yet');
        return keys.sessionId.subarray(0, SESSION_PREFIX_BYTES);
    }

    async start(): Promise<StartedHandshake> {
        const effects = await this.engine.start();
        const helloFrame = sendBytes(effects);
        const frame = decodeFrame(helloFrame);
        if (frame.type !== FrameType.RECONNECT_HELLO) {
            throw new Error('the first client effect is not a hello');
        }
        return {
            effects,
            helloFrame,
            clientHelloPayload: frame.payload,
            clientHello: decodeHello(frame.payload, Role.CLIENT),
        };
    }

    async deliverPhoneHello(payload = this.phone.helloPayload()): Promise<SessionEffect[]> {
        return this.engine.onInbound(encodeFrame(FrameType.RECONNECT_HELLO, payload));
    }

    async deliverPhoneSignature(
        transcript: Uint8Array,
        signature?: Uint8Array | Promise<Uint8Array>,
    ): Promise<SessionEffect[]> {
        const resolved = await (signature ?? this.phone.sign(transcript));
        return this.engine.onInbound(
            encodeFrame(
                FrameType.RECONNECT_SIGNATURE,
                encodeSignatureFrame({ role: Role.PHONE, signature: resolved }),
            ),
        );
    }

    /** Runs the client through hello → phone hello → phone signature and derives its keys. */
    async drive(): Promise<HandshakeDrive> {
        const started = await this.start();
        const phoneHelloPayload = this.phone.helloPayload();
        const transcript = reconnectRbase(phoneHelloPayload, started.clientHelloPayload);
        const helloEffects = await this.deliverPhoneHello(phoneHelloPayload);
        if (helloEffects.length !== 0) throw new Error('the engine rejected the phone hello');
        const phoneSignature = await this.phone.sign(transcript);
        const effects = await this.deliverPhoneSignature(transcript, phoneSignature);
        if (effects.length !== 2) {
            throw new Error(`expected signature and confirm, got ${effects.length} effects`);
        }
        const signatureFrame = decodeFrame(sendBytes(effects, 0));
        if (signatureFrame.type !== FrameType.RECONNECT_SIGNATURE) {
            throw new Error('the client did not send a signature frame');
        }
        const clientSignature = decodeSignatureFrame(signatureFrame.payload, Role.CLIENT).signature;
        const token = completionToken(effects, 1);
        const clientConfirmRecord = sendBytes(effects, 1);

        const clientPoint = await requireValidPoint(started.clientHello.ephemeralPublicKey);
        const shared = new Uint8Array(
            await crypto.subtle.deriveBits(
                { name: 'ECDH', public: clientPoint },
                this.phone.ephemeral.privateKey,
                256,
            ),
        );
        const material = await deriveKeyMaterial(
            shared,
            this.phone.nonce,
            started.clientHello.nonce,
        );
        this.keys = await importKeys(material);
        material.c2pKey.fill(0);
        material.p2cKey.fill(0);
        shared.fill(0);
        this.transcript = transcript;
        this.phoneSignature = phoneSignature;
        this.confirmInput = await confirmInput(transcript, phoneSignature, clientSignature);
        const clientConfirm = await openRecord(
            this.keys.c2pKey,
            this.keys.c2pPrefix,
            this.prefix,
            Direction.CLIENT_TO_PHONE,
            0n,
            clientConfirmRecord,
        );
        return {
            started,
            phoneHelloPayload,
            phoneSignature,
            clientSignature,
            clientConfirmRecord,
            clientConfirm,
            transcript,
            confirmInput: this.confirmInput,
            token,
        };
    }

    async confirmRecord(
        peerState: number,
        options: { confirmation?: Uint8Array; body?: Uint8Array } = {},
    ): Promise<Uint8Array> {
        const keys = this.requireKeys();
        const body =
            options.body ??
            concat(
                options.confirmation ??
                    (await confirmation(keys.phoneConfirmKey, this.confirmInput)),
                Uint8Array.of(peerState),
            );
        return sealRecord(
            keys.p2cKey,
            keys.p2cPrefix,
            this.prefix,
            Direction.PHONE_TO_CLIENT,
            RecordType.RECONNECT_CONFIRM,
            0n,
            body,
        );
    }

    async openSession(
        options: { peerState?: number } = {},
    ): Promise<{ drive: HandshakeDrive; authenticated: SessionEffect }> {
        const drive = await this.drive();
        const pending = await this.engine.onSendComplete(drive.token, true);
        if (pending.length !== 0) {
            throw new Error('the completion authenticated before the phone confirm');
        }
        const effects = await this.engine.onInbound(
            await this.confirmRecord(options.peerState ?? PEER_STATE_ACTIVE),
        );
        const authenticated = effects[0];
        if (effects.length !== 1 || authenticated?.kind !== 'authenticated') {
            throw new Error('the session did not authenticate');
        }
        return { drive, authenticated };
    }

    async phoneRecord(
        type: number,
        sequence: bigint,
        body: Uint8Array = new Uint8Array(0),
        direction: Direction = Direction.PHONE_TO_CLIENT,
    ): Promise<Uint8Array> {
        const keys = this.requireKeys();
        const phoneToClient = direction === Direction.PHONE_TO_CLIENT;
        return sealRecord(
            phoneToClient ? keys.p2cKey : keys.c2pKey,
            phoneToClient ? keys.p2cPrefix : keys.c2pPrefix,
            this.prefix,
            direction,
            type,
            sequence,
            body,
        );
    }

    /**
     * A well-formed GCM record whose type is outside §7.3, built without
     * `sealRecord`'s type check so the engine's own validation is exercised.
     */
    async phoneUncheckedRecord(
        type: number,
        sequence: bigint,
        body: Uint8Array = new Uint8Array(0),
        direction: Direction = Direction.PHONE_TO_CLIENT,
    ): Promise<Uint8Array> {
        const keys = this.requireKeys();
        const phoneToClient = direction === Direction.PHONE_TO_CLIENT;
        const key = phoneToClient ? keys.p2cKey : keys.c2pKey;
        const prefix = phoneToClient ? keys.p2cPrefix : keys.c2pPrefix;
        const header = new Uint8Array(RECORD_HEADER_BYTES);
        header[0] = HEADER_VERSION;
        header[1] = HEADER_VERSION;
        header[2] = direction;
        header[3] = (type >>> BITS_PER_BYTE) & U8_MASK;
        header[4] = type & U8_MASK;
        writeU64(header, SEQUENCE_OFFSET, sequence);
        header[LENGTH_OFFSET] = (body.length >>> BITS_PER_BYTE) & U8_MASK;
        header[LENGTH_OFFSET + 1] = body.length & U8_MASK;
        header.set(this.prefix, SESSION_OFFSET);
        const nonce = new Uint8Array(12);
        nonce.set(prefix, 0);
        writeU64(nonce, 4, sequence);
        const sealed = new Uint8Array(
            await crypto.subtle.encrypt(
                {
                    name: 'AES-GCM',
                    iv: nonce,
                    additionalData: header,
                    tagLength: GCM_TAG_BITS,
                },
                key,
                body,
            ),
        );
        if (sealed.length !== body.length + TAG_BYTES) {
            throw new Error('unexpected GCM output length');
        }
        return concat(header, sealed);
    }

    async openClientApplication(record: Uint8Array, sequence: bigint): Promise<OpenedRecord> {
        const keys = this.requireKeys();
        return openRecord(
            keys.c2pKey,
            keys.c2pPrefix,
            this.prefix,
            Direction.CLIENT_TO_PHONE,
            sequence,
            record,
        );
    }

    private requireKeys(): ReconnectKeys {
        if (this.keys === null) throw new Error('keys are not derived yet');
        return this.keys;
    }
}
