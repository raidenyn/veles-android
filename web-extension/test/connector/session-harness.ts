import { IDBFactory } from 'fake-indexeddb';

import { ConnectorApp, type ConnectorAction, type ConnectorView } from '../../src/connector';
import { ConnectorController } from '../../src/connector/controller';
import { SessionManager } from '../../src/connector/sessions';
import type { ConnectorViewModel } from '../../src/connector/view-model';
import { normalizeLowS } from '../../src/session/ecdsa';
import type { ApplicationHandler, ClientEngineDeps } from '../../src/session/engine';
import {
    decodeFrame,
    decodeSignatureFrame,
    encodeFrame,
    encodeSignatureFrame,
} from '../../src/session/frames';
import { decodeHello } from '../../src/session/hello';
import { requireValidPoint } from '../../src/session/p256';
import { openRecord, sealRecord, type OpenedRecord } from '../../src/session/record';
import {
    confirmation,
    confirmInput,
    deriveKeyMaterial,
    importKeys,
    rbase as reconnectRbase,
    type ReconnectKeys,
} from '../../src/session/schedule';
import { Direction, FrameType, RecordType, Role } from '../../src/session/wire';
import { IndexedDbIdentityStore, type StoredIdentity } from '../../src/trust/identity';
import {
    bytes,
    fingerprint,
    hex,
    type TrustSnapshot,
    type TrustState,
} from '../../src/trust/model';
import { ChromeTrustRepository, type AuthenticationVerifier } from '../../src/trust/repository';

import {
    HANDLE_CONNECTION,
    HANDLE_DEVICE,
    connectedEvent,
    disconnectedEvent,
    messageEvent,
    readyEvent,
    sentEvent,
} from './fixtures';
import { MockNativePort } from './mock-port';
import { PEER_STATE_ACTIVE, TestPhone, hexOf } from '../session/client-harness';

/**
 * Monotonic clock with the connector's injected timer surface. The engine only
 * reads `now()`, while the controller and reconnect policy use the timers.
 */
export class SessionClock {
    value = 1_000;
    private nextId = 1;
    private readonly tasks = new Map<number, { due: number; run: () => void }>();

    readonly now = (): number => this.value;

    readonly setTimeout = (fn: () => void, ms: number): number => {
        const id = this.nextId;
        this.nextId += 1;
        this.tasks.set(id, { due: this.value + ms, run: fn });
        return id;
    };

    readonly clearTimeout = (handle: unknown): void => {
        if (typeof handle === 'number') this.tasks.delete(handle);
    };

    advance(ms: number): void {
        this.value += ms;
        for (const [id, task] of [...this.tasks]) {
            if (task.due <= this.value) {
                this.tasks.delete(id);
                task.run();
            }
        }
    }

    get pendingTimers(): number {
        return this.tasks.size;
    }
}

// Shared scripted-phone harness for the connector session tests.
//
// The harness never supplies a second authentication verifier: the one
// production `ChromeTrustRepository` is created with a delegating verifier that
// the connector/session manager fills in, exactly like the production wiring.
// The phone half uses the production codecs so a successful handshake proves
// the real transcript, key schedule and confirmation checks.

export async function flush(times = 10): Promise<void> {
    // Web Crypto resolves on the libuv thread pool, so a pure microtask flush
    // cannot observe a completed handshake or record open. Interleave real
    // macrotasks (the injected connector clock is unaffected).
    for (let index = 0; index < times; index += 1) {
        for (let tick = 0; tick < 5; tick += 1) await Promise.resolve();
        await new Promise<void>((resolve) => setTimeout(resolve, 0));
    }
}

export function deferred<T = void>(): { promise: Promise<T>; resolve: (value: T) => void } {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((settle) => {
        resolve = settle;
    });
    return { promise, resolve };
}

export interface TestTrust {
    repository: ChromeTrustRepository;
    keys: IndexedDbIdentityStore;
    identity: StoredIdentity;
    bindVerifier(verifier: AuthenticationVerifier): void;
    addPeer(
        phone: TestPhone,
        options?: { state?: TrustState; hint?: string; label?: string },
    ): Promise<string>;
    peers(): ReturnType<ChromeTrustRepository['peers']>;
    /** A new repository over the same durable identity, snapshot and lock. */
    clone(): TestTrust;
}

interface TrustStorage {
    keys: IndexedDbIdentityStore;
    identity: StoredIdentity;
    read(): TrustSnapshot;
    write(snapshot: TrustSnapshot): Promise<void>;
    readonly lock: <T>(operation: () => Promise<T>) => Promise<T>;
    readonly clock: { now(): number };
}

async function createTrustStorage(clock: { now(): number }): Promise<TrustStorage> {
    const keys = new IndexedDbIdentityStore(new IDBFactory());
    const identity = await keys.create();
    let snapshot: TrustSnapshot = {
        version: 1,
        identity: { installationId: identity.installationId, publicKey: identity.publicKey },
        peers: [],
    };
    let chain: Promise<unknown> = Promise.resolve();
    return {
        keys,
        identity,
        clock,
        read: () => structuredClone(snapshot),
        async write(next) {
            snapshot = structuredClone(next);
        },
        lock: async <T>(operation: () => Promise<T>): Promise<T> => {
            const result = chain.then(operation);
            chain = result.then(
                () => undefined,
                () => undefined,
            );
            return result;
        },
    };
}

function buildTrust(storage: TrustStorage): TestTrust {
    const holder: { verifier: AuthenticationVerifier } = {
        verifier: async () => false,
    };
    const local = {
        restrict: async (): Promise<void> => undefined,
        read: async (): Promise<unknown> => storage.read(),
        write: async (next: TrustSnapshot): Promise<void> => storage.write(next),
        clear: async (): Promise<void> => {
            await storage.write({
                version: 1,
                identity: {
                    installationId: storage.identity.installationId,
                    publicKey: storage.identity.publicKey,
                },
                peers: [],
            });
        },
    };
    const repository = new ChromeTrustRepository(
        storage.keys,
        local,
        storage.lock,
        (peer, stage, proof) => holder.verifier(peer, stage, proof),
        storage.clock.now,
        () => 10_000,
        { id: crypto.randomUUID(), isAlive: async () => true },
    );
    return {
        repository,
        keys: storage.keys,
        identity: storage.identity,
        bindVerifier(verifier) {
            holder.verifier = verifier;
        },
        async addPeer(phone, options = {}) {
            const peerId = hexOf(phone.installationId);
            const snapshot = storage.read();
            snapshot.peers.push({
                installationId: peerId,
                publicKey: hex(phone.publicKey),
                label: options.label ?? 'Pixel test phone',
                deviceType: 0,
                enrollmentTranscript: 'ab'.repeat(32),
                fingerprint: await fingerprint(hex(phone.publicKey), storage.identity.publicKey),
                state: options.state ?? 'active',
                createdAtMillis: 1_000,
                lastSeenMillis: null,
                pendingContext: null,
                pendingSince: null,
                ...(options.hint === undefined ? {} : { platformHint: options.hint }),
            });
            await storage.write(snapshot);
            return peerId;
        },
        peers() {
            return repository.peers();
        },
        clone() {
            return buildTrust(storage);
        },
    };
}

export async function createTestTrust(
    clock: { now(): number } = new SessionClock(),
): Promise<TestTrust> {
    return buildTrust(await createTrustStorage(clock));
}

/** Engine dependencies backed by the one production repository and identity. */
export function sessionEngineDeps(
    trust: TestTrust,
    clock: { now(): number },
    localInstallationId: Uint8Array,
    options: { handler?: ApplicationHandler } = {},
): (handle: string, peerId: string) => ClientEngineDeps {
    return (_handle, peerId) => ({
        localInstallationId,
        expectedPeerId: peerId,
        isCurrent: () => true,
        lookupPhone: async (id) => {
            const peers = await trust.repository.peers();
            const peer = peers.find((candidate) => candidate.installationId === id);
            if (peer === undefined) return undefined;
            return { publicKey: bytes(peer.publicKey, 65), state: peer.state };
        },
        upgradeMirror: (id, proof) => trust.repository.confirm(id, 'phone_active', proof),
        now: clock.now,
        sign: async (message) => {
            const identity = await trust.keys.read();
            if (identity === undefined) throw new Error('identity unavailable');
            return normalizeLowS(
                new Uint8Array(
                    await crypto.subtle.sign(
                        { name: 'ECDSA', hash: 'SHA-256' },
                        identity.privateKey,
                        message,
                    ),
                ),
            );
        },
        ...(options.handler === undefined ? {} : { handler: options.handler }),
    });
}

/** The scripted phone half of one reconnect, using the production codecs. */
export class PhoneSide {
    private transcript: Uint8Array | null = null;
    private phoneSignature: Uint8Array | null = null;
    private clientSignature: Uint8Array | null = null;
    private sessionKeys: ReconnectKeys | null = null;
    private confirmInputBytes: Uint8Array | null = null;

    constructor(readonly phone: TestPhone) {}

    get keys(): ReconnectKeys {
        if (this.sessionKeys === null) throw new Error('phone keys are not derived yet');
        return this.sessionKeys;
    }

    get prefix(): Uint8Array {
        return this.keys.sessionId.slice(0, 8);
    }

    async acceptClientHello(payload: Uint8Array): Promise<Uint8Array> {
        const clientHello = decodeHello(payload, Role.CLIENT);
        this.transcript = reconnectRbase(this.phone.helloPayload(), payload);
        const clientPoint = await requireValidPoint(clientHello.ephemeralPublicKey);
        const shared = new Uint8Array(
            await crypto.subtle.deriveBits(
                { name: 'ECDH', public: clientPoint },
                this.phone.ephemeral.privateKey,
                256,
            ),
        );
        const material = await deriveKeyMaterial(shared, this.phone.nonce, clientHello.nonce);
        this.sessionKeys = await importKeys(material);
        material.c2pKey.fill(0);
        material.p2cKey.fill(0);
        shared.fill(0);
        return encodeFrame(FrameType.RECONNECT_HELLO, this.phone.helloPayload());
    }

    async signatureFrame(): Promise<Uint8Array> {
        if (this.transcript === null) throw new Error('no client hello');
        this.phoneSignature = await this.phone.sign(this.transcript);
        return encodeFrame(
            FrameType.RECONNECT_SIGNATURE,
            encodeSignatureFrame({ role: Role.PHONE, signature: this.phoneSignature }),
        );
    }

    async acceptClientSignature(payload: Uint8Array): Promise<void> {
        if (this.transcript === null || this.phoneSignature === null) {
            throw new Error('phone signature is not sent yet');
        }
        this.clientSignature = decodeSignatureFrame(payload, Role.CLIENT).signature;
        this.confirmInputBytes = await confirmInput(
            this.transcript,
            this.phoneSignature,
            this.clientSignature,
        );
    }

    async confirmFrame(
        peerState: number = PEER_STATE_ACTIVE,
        options: { confirmation?: Uint8Array } = {},
    ): Promise<Uint8Array> {
        if (this.confirmInputBytes === null) throw new Error('no client signature');
        const hash =
            options.confirmation ??
            (await confirmation(this.keys.phoneConfirmKey, this.confirmInputBytes));
        const body = new Uint8Array(33);
        body.set(hash, 0);
        body[32] = peerState;
        return sealRecord(
            this.keys.p2cKey,
            this.keys.p2cPrefix,
            this.prefix,
            Direction.PHONE_TO_CLIENT,
            RecordType.RECONNECT_CONFIRM,
            0n,
            body,
        );
    }

    async applicationFrame(
        type: number,
        sequence: bigint,
        body: Uint8Array = new Uint8Array(0),
    ): Promise<Uint8Array> {
        return sealRecord(
            this.keys.p2cKey,
            this.keys.p2cPrefix,
            this.prefix,
            Direction.PHONE_TO_CLIENT,
            type,
            sequence,
            body,
        );
    }

    async openClientRecord(record: Uint8Array, sequence: bigint): Promise<OpenedRecord> {
        return openRecord(
            this.keys.c2pKey,
            this.keys.c2pPrefix,
            this.prefix,
            Direction.CLIENT_TO_PHONE,
            sequence,
            record,
        );
    }
}

export interface PostedSend {
    requestId: string;
    handle: string;
    bytes: Uint8Array;
}

export async function nextSend(port: MockNativePort): Promise<PostedSend> {
    const message = await port.nextPosted();
    if (message.type !== 'send') {
        throw new Error(`expected a send post, got ${String(message.type)}`);
    }
    return {
        requestId: message.request_id as string,
        handle: message.connection_handle as string,
        bytes: Uint8Array.from(Buffer.from(message.record_base64 as string, 'base64')),
    };
}

export async function ackSend(port: MockNativePort, send: PostedSend): Promise<void> {
    await port.emit(sentEvent({ request_id: send.requestId, connection_handle: send.handle }));
}

/** Answers the connector-initiated disconnect that follows a rejected session. */
export async function ackDisconnect(port: MockNativePort, connectionHandle: string): Promise<void> {
    const message = await port.nextPosted();
    if (message.type !== 'disconnect') {
        throw new Error(`expected a disconnect post, got ${String(message.type)}`);
    }
    await port.emit(
        disconnectedEvent({
            request_id: message.request_id as string,
            connection_handle: connectionHandle,
        }),
    );
}

export async function emitRecord(
    port: MockNativePort,
    connectionHandle: string,
    bytesValue: Uint8Array,
): Promise<void> {
    await port.emit(
        messageEvent({
            connection_handle: connectionHandle,
            record_base64: Buffer.from(bytesValue).toString('base64'),
        }),
    );
}

export function postedSends(port: MockNativePort, handle?: string): PostedSend[] {
    return port.posted
        .filter(
            (message): message is Record<string, unknown> =>
                typeof message === 'object' &&
                message !== null &&
                (message as { type?: unknown }).type === 'send' &&
                (handle === undefined ||
                    (message as { connection_handle?: unknown }).connection_handle === handle),
        )
        .map((message) => ({
            requestId: message.request_id as string,
            handle: message.connection_handle as string,
            bytes: Uint8Array.from(Buffer.from(message.record_base64 as string, 'base64')),
        }));
}

export interface TestController {
    controller: ConnectorController;
    clock: SessionClock;
    ports: MockNativePort[];
    currentPort(): MockNativePort;
}

export function createTestController(): TestController {
    const clock = new SessionClock();
    const ports: MockNativePort[] = [];
    const controller = new ConnectorController({
        connectNative: () => {
            const port = new MockNativePort();
            ports.push(port);
            return port;
        },
        lastError: () => ports.at(-1)?.lastError,
        now: clock.now,
        setTimeout: clock.setTimeout,
        clearTimeout: clock.clearTimeout,
        requestTimeoutMs: 30_000,
    });
    return {
        controller,
        clock,
        ports,
        currentPort(): MockNativePort {
            const port = ports.at(-1);
            if (port === undefined) throw new Error('no port created');
            return port;
        },
    };
}

export async function negotiateHost(context: TestController): Promise<void> {
    context.controller.open();
    const hello = context.controller.hello();
    const posted = await context.currentPort().nextPosted();
    await context.currentPort().emit(readyEvent({ request_id: posted.request_id as string }));
    const result = await hello;
    if (!result.ok) throw new Error('host negotiation failed');
}

export interface ManagerFixture extends TestController {
    trust: TestTrust;
    manager: SessionManager;
    phone: TestPhone;
    peerId: string;
}

export async function createManagerFixture(
    options: {
        phone?: TestPhone;
        state?: TrustState;
        hint?: string;
        handler?: ApplicationHandler;
    } = {},
): Promise<ManagerFixture> {
    const context = createTestController();
    const trust = await createTestTrust(context.clock);
    const phone = options.phone ?? (await TestPhone.create());
    const peerId = await trust.addPeer(phone, {
        state: options.state ?? 'active',
        hint: options.hint,
    });
    const managerRef: { current?: SessionManager } = {};
    trust.bindVerifier(async (peer, stage, proof) =>
        managerRef.current === undefined ? false : managerRef.current.verifier(peer, stage, proof),
    );
    const deps = sessionEngineDeps(
        trust,
        context.clock,
        bytes(trust.identity.installationId, 16),
        options.handler === undefined ? {} : { handler: options.handler },
    );
    const manager = new SessionManager(
        context.controller,
        trust.repository,
        (handle, peer) => deps(handle, peer),
        () => context.controller.portGeneration,
    );
    managerRef.current = manager;
    await negotiateHost(context);
    return { ...context, trust, manager, phone, peerId };
}

export async function connectForSession(
    context: TestController,
    connectionHandle: string = HANDLE_CONNECTION,
    deviceHandle: string = HANDLE_DEVICE,
): Promise<void> {
    const request = context.controller.connect({ deviceHandle });
    const posted = await context.currentPort().nextPosted();
    await context.currentPort().emit(
        connectedEvent({
            request_id: posted.request_id as string,
            connection_handle: connectionHandle,
        }),
    );
    const result = await request;
    if (!result.ok) throw new Error('transport connect failed');
}

/** Drives the phone half of a handshake over the mock port. */
export async function answerHandshake(
    context: TestController,
    options: {
        phone: TestPhone;
        connectionHandle?: string;
        peerState?: number;
        ackConfirm?: boolean;
        hello?: PostedSend;
        beforeConfirm?: (side: PhoneSide, clientConfirm: PostedSend) => Promise<void>;
    },
): Promise<PhoneSide> {
    const handle = options.connectionHandle ?? HANDLE_CONNECTION;
    const port = context.currentPort();
    const hello = options.hello ?? (await nextSend(port));
    if (hello.bytes[0] !== FrameType.RECONNECT_HELLO) {
        throw new Error('the first client send is not a hello frame');
    }
    await ackSend(port, hello);
    const side = new PhoneSide(options.phone);
    const phoneHello = await side.acceptClientHello(decodeFrame(hello.bytes).payload);
    await emitRecord(port, handle, phoneHello);
    await emitRecord(port, handle, await side.signatureFrame());
    await flush();
    const clientSignature = await nextSend(port);
    await side.acceptClientSignature(decodeFrame(clientSignature.bytes).payload);
    await ackSend(port, clientSignature);
    const clientConfirm = await nextSend(port);
    if (options.beforeConfirm !== undefined) await options.beforeConfirm(side, clientConfirm);
    const ackConfirm = options.ackConfirm ?? true;
    if (ackConfirm) await ackSend(port, clientConfirm);
    await emitRecord(port, handle, await side.confirmFrame(options.peerState ?? PEER_STATE_ACTIVE));
    await flush();
    if (!ackConfirm) {
        await ackSend(port, clientConfirm);
        await flush();
    }
    return side;
}

export class TestView implements ConnectorView {
    readonly models: ConnectorViewModel[] = [];
    private listener: ((action: ConnectorAction) => void) | null = null;

    render(viewModel: ConnectorViewModel): void {
        this.models.push(viewModel);
    }

    onAction(listener: (action: ConnectorAction) => void): () => void {
        this.listener = listener;
        return () => {
            if (this.listener === listener) this.listener = null;
        };
    }

    emit(action: ConnectorAction): void {
        this.listener?.(action);
    }

    get last(): ConnectorViewModel {
        const model = this.models.at(-1);
        if (model === undefined) throw new Error('no view model rendered');
        return model;
    }
}

export interface AppFixture extends TestController {
    app: ConnectorApp;
    view: TestView;
    trust: TestTrust;
    phone: TestPhone;
    peerId: string;
    extra: { phone: TestPhone; peerId: string }[];
}

export interface AppFixtureOptions {
    trust?: TestTrust;
    phone?: TestPhone;
    state?: TrustState;
    hint?: string;
    handler?: ApplicationHandler;
    extraPhones?: number;
}

export async function createAppFixture(options: AppFixtureOptions = {}): Promise<AppFixture> {
    const context = createTestController();
    const trust = options.trust ?? (await createTestTrust(context.clock));
    const phone = options.phone ?? (await TestPhone.create());
    let peerId = hexOf(phone.installationId);
    if (options.trust === undefined) {
        peerId = await trust.addPeer(phone, {
            state: options.state ?? 'active',
            hint: options.hint,
        });
    }
    const extra: { phone: TestPhone; peerId: string }[] = [];
    for (let index = 0; index < (options.extraPhones ?? 0); index += 1) {
        const other = await TestPhone.create();
        const otherId = await trust.addPeer(other, {
            state: options.state ?? 'active',
            label: `Second phone ${index + 1}`,
        });
        extra.push({ phone: other, peerId: otherId });
    }
    const view = new TestView();
    const app = new ConnectorApp({
        controller: context.controller,
        view,
        setTimeout: context.clock.setTimeout,
        clearTimeout: context.clock.clearTimeout,
        session: {
            createRepository(verifier) {
                trust.bindVerifier(verifier);
                return trust.repository;
            },
            createEngineDeps(handle, peer, localInstallationId) {
                return sessionEngineDeps(
                    trust,
                    context.clock,
                    localInstallationId,
                    options.handler === undefined ? {} : { handler: options.handler },
                )(handle, peer);
            },
        },
    });
    return { ...context, app, view, trust, phone, peerId, extra };
}

export async function startApp(fixture: AppFixture): Promise<void> {
    const started = fixture.app.start();
    const posted = await fixture.currentPort().nextPosted();
    await fixture.currentPort().emit(readyEvent({ request_id: posted.request_id as string }));
    await started;
    await flush();
}

/**
 * Starts a manual Connect and answers the transport `connected` event. The
 * returned promise settles only when the connector's session work does.
 */
export interface ManualConnect {
    /** Settles only when the connector's session work completes. */
    action: Promise<void>;
    connectionHandle: string;
}

export async function manualConnect(
    fixture: AppFixture,
    options: {
        deviceHandle?: string;
        peerId?: string;
        connectionHandle?: string;
        hint?: string;
    } = {},
): Promise<ManualConnect> {
    const deviceHandle = options.deviceHandle ?? HANDLE_DEVICE;
    const connectionHandle = options.connectionHandle ?? HANDLE_CONNECTION;
    const action = fixture.app.handleAction({
        type: 'connect',
        deviceHandle,
        ...(options.peerId === undefined ? {} : { peerId: options.peerId }),
    });
    const posted = await fixture.currentPort().nextPosted();
    if (posted.type !== 'connect') {
        throw new Error(`expected a connect request, got ${String(posted.type)}`);
    }
    await fixture.currentPort().emit(
        connectedEvent({
            request_id: posted.request_id as string,
            connection_handle: connectionHandle,
            ...(options.hint === undefined ? {} : { platform_hint: options.hint }),
        }),
    );
    return { action, connectionHandle };
}

export function decodeRecordHeader(record: Uint8Array): {
    type: number;
    direction: number;
    sequence: bigint;
    prefix: Uint8Array;
} | null {
    if (record.length < 23) return null;
    if (record[0] !== 1 || record[1] !== 1) return null;
    let sequence = 0n;
    for (let index = 5; index < 13; index += 1) sequence = (sequence << 8n) | BigInt(record[index]);
    const ciphertextLength = ((record[13] ?? 0) << 8) | (record[14] ?? 0);
    if (record.length !== 23 + ciphertextLength + 16) return null;
    return {
        type: ((record[3] ?? 0) << 8) | (record[4] ?? 0),
        direction: record[2] ?? 0,
        sequence,
        prefix: record.slice(15, 23),
    };
}
