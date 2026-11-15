import { readFileSync } from 'node:fs';

import { beforeAll, describe, expect, it } from 'vitest';

import { ConnectorApp, type ConnectorView } from '../../src/connector';
import {
    createRevocationCoordinator,
    type RevocationCoordinator,
    type TrustStatus,
} from '../../src/connector/revocation';
import { SessionManager } from '../../src/connector/sessions';
import { ActivationEngine, composeHandlers } from '../../src/pairing/activation';
import { initPairingCore } from '../../src/pairing/core';
import { fingerprintDisplay } from '../../src/pairing/enrollment';
import { PairingFrameType, encodePairingFrame } from '../../src/pairing/frames';
import { PairingLink, type PairingTransport } from '../../src/pairing/link';
import { ProofRegistry } from '../../src/pairing/proofs';
import { normalizeLowS } from '../../src/session/ecdsa';
import type { ApplicationHandler, ClientEngineDeps } from '../../src/session/engine';
import { decodeFrame } from '../../src/session/frames';
import { concat, hmacSha256 } from '../../src/session/schedule';
import { createRemovalHandler, type RemovalOperations } from '../../src/session/removal';
import { RecordType, SessionClass } from '../../src/session/wire';
import type { IdentityStore, StoredIdentity } from '../../src/trust/identity';
import {
    TrustError,
    bytes,
    fingerprint,
    hex,
    type TrustSnapshot,
    type TrustState,
} from '../../src/trust/model';
import { ChromeTrustRepository, type AuthenticationVerifier } from '../../src/trust/repository';
import type { ExclusiveLock, LocalStore } from '../../src/trust/storage';

import { PACKAGE_BYTES, PhonePeer, hexOf } from '../pairing/enrollment-harness';
import { PEER_STATE_BELOW_ACTIVE, TestPhone } from '../session/client-harness';
import {
    HANDLE_CONNECTION,
    HANDLE_CONNECTION_2,
    HANDLE_DEVICE,
    HANDLE_DEVICE_2,
    connectedEvent,
    readyEvent,
} from './fixtures';
import type { MockNativePort } from './mock-port';
import {
    SessionClock,
    ackSend,
    answerHandshake,
    connectForSession,
    createTestController,
    deferred,
    emitRecord,
    PhoneSide,
    ackDisconnect,
    flush,
    negotiateHost,
    nextSend,
    type TestController,
} from './session-harness';

// OTP-11 Task 10: the revocation flows end to end on the production-shaped Chrome stack (design
// D2/D3/D5/D6/D7/D8): the real `ChromeTrustRepository` over in-memory storage, the real
// `SessionManager`, the real enrollment link and client machine, the real activation handler and
// the real coordinator, with a fake BLE transport in place of the Native Messaging port.
//
// Every wait is event-driven (the next posted request or a settled promise); nothing sleeps and
// nothing reads the wall clock.

const CODE = '000042';
const OWNER_ID = '0f1d2c3b-4a59-4e6f-8a7b-9c0d1e2f3a4b';
const PAIR_HANDLE = 'c0000000000000f1';
const PAIR_DEVICE = 'b0000000000000f1';
const PAIR_DEVICE_2 = 'b0000000000000f2';
const HANDSHAKE_HANDLE = 'c000000000000003';
const HANDSHAKE_DEVICE = 'b000000000000003';
const ET = new Uint8Array(32).fill(0x42);
const STAGE_PREPARE = 0;
const STAGE_COMPLETE = 2;

beforeAll(async () => {
    await initPairingCore(readFileSync(PACKAGE_BYTES));
});

async function microFlush(times = 24): Promise<void> {
    for (let index = 0; index < times; index += 1) await Promise.resolve();
}

function requireValue<T>(value: T | undefined | null): T {
    if (value === undefined || value === null) throw new Error('expected a value');
    return value;
}

/** The in-memory durable store, with the faults the store-failure tests need. */
class MemoryLocal implements LocalStore {
    snapshot: TrustSnapshot | undefined;
    writeCount = 0;
    writes: TrustSnapshot[] = [];
    clearCalls = 0;
    readFault: Error | null = null;
    writeFaultAt: number | null = null;
    /** Holds the given 1-based invalid-marker write (the first write of an identity reset). */
    holdMarkerWrite: number | null = null;
    writeGate: Promise<void> | null = null;
    markerWrites = 0;
    private readonly writeWaiters: (() => void)[] = [];

    /** Resolves when the held marker write is entered; one waiter per held write. */
    nextWriteEntered(): Promise<void> {
        return new Promise<void>((resolve) => this.writeWaiters.push(resolve));
    }

    async restrict(): Promise<void> {
        return undefined;
    }

    async read(): Promise<unknown> {
        if (this.readFault !== null) throw this.readFault;
        return this.snapshot === undefined ? undefined : structuredClone(this.snapshot);
    }

    async write(next: TrustSnapshot): Promise<void> {
        const marker = next.identity.installationId === '' && next.identity.publicKey === '';
        if (marker) this.markerWrites += 1;
        if (this.writeGate !== null && marker && this.holdMarkerWrite === this.markerWrites) {
            for (const resolve of this.writeWaiters.splice(0)) resolve();
            await this.writeGate;
        }
        this.writeCount += 1;
        if (this.writeFaultAt === this.writeCount) throw new Error('durable write failed');
        this.writes.push(structuredClone(next));
        this.snapshot = structuredClone(next);
    }

    async clear(): Promise<void> {
        this.clearCalls += 1;
        this.snapshot = undefined;
    }
}

/** The installation key store; `failure` makes `read()` an invalid identity. */
class TestKeys implements IdentityStore {
    value: StoredIdentity | undefined;
    reads = 0;
    failure: Error | null = null;

    async read(): Promise<StoredIdentity | undefined> {
        this.reads += 1;
        if (this.failure !== null) throw this.failure;
        return this.value;
    }

    async create(): Promise<StoredIdentity> {
        const pair = (await crypto.subtle.generateKey(
            { name: 'ECDSA', namedCurve: 'P-256' },
            false,
            ['sign', 'verify'],
        )) as CryptoKeyPair;
        const value: StoredIdentity = {
            installationId: hex(crypto.getRandomValues(new Uint8Array(16))),
            publicKey: hex(new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey))),
            privateKey: pair.privateKey,
        };
        this.value = value;
        return value;
    }

    async clear(): Promise<void> {
        this.value = undefined;
    }
}

function lock(): ExclusiveLock {
    let chain: Promise<unknown> = Promise.resolve();
    return <T>(operation: () => Promise<T>): Promise<T> => {
        const result = chain.then(operation);
        chain = result.then(
            () => undefined,
            () => undefined,
        );
        return result;
    };
}

interface PeerIdentity {
    installationId: Uint8Array;
    publicKey: Uint8Array;
}

interface Stack {
    controller: TestController;
    clock: SessionClock;
    local: MemoryLocal;
    keys: TestKeys;
    proofs: ProofRegistry;
    repository: ChromeTrustRepository;
    manager: SessionManager;
    coordinator: RevocationCoordinator & RemovalOperations;
    statuses: TrustStatus[];
    cleaned: string[];
    links: PairingLink[];
    handler: ApplicationHandler | undefined;
    bindVerifier(verifier: AuthenticationVerifier): void;
}

async function createStack(): Promise<Stack> {
    const controller = createTestController();
    const keys = new TestKeys();
    const local = new MemoryLocal();
    const proofs = new ProofRegistry();
    const verifier: { current: AuthenticationVerifier } = { current: async () => false };
    const failure: { action: (() => void) | null } = { action: null };
    const repository = new ChromeTrustRepository(
        keys,
        local,
        lock(),
        (peer, stage, proof) => verifier.current(peer, stage, proof),
        controller.clock.now,
        () => 1_000,
        { id: OWNER_ID, isAlive: async () => true },
        () => {
            const action = failure.action;
            if (action === null) throw new Error('store failure before fail-closed is wired');
            action();
        },
    );
    // The first use creates the identity and the durable snapshot, exactly as production does.
    const created = await repository.identity();
    const identity = bytes(created.installationId, 16);
    const statuses: TrustStatus[] = [];
    const cleaned: string[] = [];
    const links: PairingLink[] = [];
    const stack: Stack = {
        controller,
        clock: controller.clock,
        local,
        keys,
        proofs,
        repository,
        manager: undefined as unknown as SessionManager,
        coordinator: undefined as unknown as RevocationCoordinator & RemovalOperations,
        statuses,
        cleaned,
        links,
        handler: undefined,
        bindVerifier(next) {
            verifier.current = next;
        },
    };
    stack.manager = new SessionManager(
        controller.controller,
        repository,
        (handle, peerId) => engineDeps(stack, handle, peerId, identity),
        () => controller.controller.portGeneration,
        { setTimeout: controller.clock.setTimeout, clearTimeout: controller.clock.clearTimeout },
        (sessionId) => proofs.revoke(sessionId),
        () => links,
    );
    verifier.current = async (peer, stage, proof) =>
        stage === 'complete'
            ? proofs.consume(peer.installationId, stage, proof)
            : stack.manager.verifier(peer, stage, proof);
    stack.coordinator = createRevocationCoordinator({
        repo: repository,
        sessions: stack.manager,
        onStatus: (status) => statuses.push(status),
        historyCleanup: (peerId) => cleaned.push(peerId),
    });
    failure.action = () => stack.coordinator.failClosed();
    await negotiateHost(controller);
    return stack;
}

/** The production engine wiring: the real client engine over this store and its identity. */
function engineDeps(
    stack: Stack,
    _handle: string,
    peerId: string,
    identity: Uint8Array,
): ClientEngineDeps {
    return {
        localInstallationId: identity,
        expectedPeerId: peerId,
        isCurrent: () => true,
        lookupPhone: async (id) => {
            const peers = await stack.repository.peers();
            const peer = peers.find((candidate) => candidate.installationId === id);
            if (peer === undefined) return undefined;
            return {
                publicKey: bytes(peer.publicKey, 65),
                state: peer.state,
                // OTP2-CRY-019f: the public `ET` persisted with the record binds the recovery keys.
                et: bytes(peer.enrollmentTranscript, 32),
            };
        },
        upgradeMirror: (id, proof) => stack.repository.confirm(id, 'phone_active', proof),
        now: stack.clock.now,
        sign: async (message) => {
            const stored = requireValue(stack.keys.value);
            return normalizeLowS(
                new Uint8Array(
                    await crypto.subtle.sign(
                        { name: 'ECDSA', hash: 'SHA-256' },
                        stored.privateKey,
                        message,
                    ),
                ),
            );
        },
        ...(stack.handler === undefined ? {} : { handler: stack.handler }),
    };
}

/** The durable store and identity a peer record needs; a stack or an app fixture satisfies it. */
interface PeerStore {
    keys: TestKeys;
    local: MemoryLocal;
    clock: { now(): number };
}

/** Writes one durable record the way a completed enrollment or an earlier pairing left it. */
async function addPeer(
    store: PeerStore,
    peer: PeerIdentity,
    options: {
        state?: TrustState;
        hint?: string;
        label?: string;
        et?: Uint8Array;
    } = {},
): Promise<string> {
    const identity = requireValue(store.keys.value);
    const installationId = hex(peer.installationId);
    const publicKey = hex(peer.publicKey);
    const state = options.state ?? 'active';
    const snapshot: TrustSnapshot = store.local.snapshot ?? {
        version: 1,
        identity: { installationId: identity.installationId, publicKey: identity.publicKey },
        peers: [],
    };
    snapshot.peers = snapshot.peers.filter(
        (candidate) => candidate.installationId !== installationId,
    );
    snapshot.peers.push({
        installationId,
        publicKey,
        label: options.label ?? 'Pixel test phone',
        deviceType: 0,
        enrollmentTranscript: hex(options.et ?? ET),
        fingerprint: await fingerprint(publicKey, identity.publicKey),
        state,
        createdAtMillis: 1_000,
        lastSeenMillis: state === 'pending' ? null : 2_000,
        pendingContext: state === 'pending' ? OWNER_ID : null,
        pendingSince: state === 'pending' ? store.clock.now() : null,
        ...(options.hint === undefined || state === 'pending'
            ? {}
            : { platformHint: options.hint }),
    });
    store.local.snapshot = snapshot;
    return installationId;
}

function terminations(stack: Stack, handle: string): string[] {
    return stack.controller.controller.state.diagnostics
        .entries()
        .filter(
            (entry) => entry.type === 'session_terminated' && entry.connection_handle === handle,
        )
        .map((entry) => entry.code ?? '');
}

function postsOf(port: MockNativePort, type: string): Record<string, unknown>[] {
    return port.posted.filter(
        (message): message is Record<string, unknown> =>
            typeof message === 'object' &&
            message !== null &&
            (message as { type?: unknown }).type === type,
    );
}

/** The wire type of every posted send for one handle (frame types included). */
function postedTypes(port: MockNativePort, handle: string): number[] {
    return postsOf(port, 'send')
        .filter((message) => message.connection_handle === handle)
        .map((message) => {
            const record = Uint8Array.from(Buffer.from(message.record_base64 as string, 'base64'));
            return ((record[3] ?? 0) << 8) | (record[4] ?? 0);
        });
}

async function openSession(
    stack: Stack,
    peer: { phone: TestPhone; peerId: string },
    handle: string = HANDLE_CONNECTION,
    deviceHandle: string = HANDLE_DEVICE,
    et?: Uint8Array,
    peerState?: number,
): Promise<PhoneSide> {
    await connectForSession(stack.controller, handle, deviceHandle);
    const opened = stack.manager.open(handle, peer.peerId);
    const side = await answerHandshake(stack.controller, {
        phone: peer.phone,
        connectionHandle: handle,
        ...(et === undefined ? {} : { et }),
        ...(peerState === undefined ? {} : { peerState }),
    });
    await expect(opened).resolves.toEqual({ result: 'authenticated', peerId: peer.peerId });
    return side;
}

/** A direct enrollment transport: the pairing link never touches the Native Messaging port. */
class FakeTransport implements PairingTransport {
    readonly sent: Uint8Array[] = [];
    disconnectCount = 0;

    async connect(target: { deviceHandle: string }) {
        return {
            ok: true as const,
            event: {
                type: 'connected' as const,
                envelope_version: 1 as const,
                api_version: 1,
                request_id: `connect-${target.deviceHandle}`,
                connection_handle: `link-${target.deviceHandle}`,
            },
        };
    }

    async send(handle: string, record: Uint8Array) {
        this.sent.push(record.slice());
        return {
            ok: true as const,
            event: {
                type: 'sent' as const,
                envelope_version: 1 as const,
                api_version: 1,
                request_id: `send-${this.sent.length}`,
                connection_handle: handle,
            },
        };
    }

    async disconnect(handle: string) {
        this.disconnectCount += 1;
        return {
            ok: true as const,
            event: {
                type: 'disconnected' as const,
                envelope_version: 1 as const,
                api_version: 1,
                connection_handle: handle,
            },
        };
    }

    onRecord(): () => void {
        return () => undefined;
    }
}

async function startLink(
    stack: Stack,
    options: {
        repo?: PairingLinkRepository;
        code?: string;
        deviceHandle?: string;
        isAborted?: () => boolean;
    } = {},
): Promise<{ link: PairingLink; transport: FakeTransport }> {
    const transport = new FakeTransport();
    const link = await PairingLink.start({
        controller: transport,
        repo: options.repo ?? stack.repository,
        proofs: stack.proofs,
        code: options.code ?? CODE,
        clock: stack.clock.now,
        deviceHandle: options.deviceHandle ?? 'device-link',
        tickIntervalMs: 0,
        ...(options.isAborted === undefined ? {} : { isAborted: options.isAborted }),
    });
    // The coordinator selects a live enrollment link by its bound peer id; the manager's
    // registry is how a directly started link becomes visible to it (as the app's is).
    stack.links.push(link);
    return { link, transport };
}

/** The client-side repository surface the link needs; a test can gate `addPending`. */
interface PairingLinkRepository {
    identity(): ReturnType<ChromeTrustRepository['identity']>;
    peers(): ReturnType<ChromeTrustRepository['peers']>;
    addPending(input: Parameters<ChromeTrustRepository['addPending']>[0]): Promise<void>;
    confirm(...args: Parameters<ChromeTrustRepository['confirm']>): Promise<void>;
    revoke(peerId: string): Promise<void>;
    revokePending(peerId: string): Promise<boolean>;
}

/** Drives the enrollment to the durable pending record, as OTP2-STA-010a requires. */
async function driveToPending(
    link: PairingLink,
    transport: FakeTransport,
    phone: PhonePeer,
): Promise<void> {
    await link.receive(phone.offerFor(transport.sent[0]));
    const cA = phone.acceptPoint(transport.sent[1]);
    await link.receive(encodePairingFrame(PairingFrameType.CA, cA));
    await phone.acceptConfirmB(transport.sent[2]);
    await link.receive(await phone.identityFrame());
    await phone.acceptClientIdentity(transport.sent[3]);
}

/** Drives the enrollment through the verified commit to the active record. */
async function driveToActive(
    link: PairingLink,
    transport: FakeTransport,
    phone: PhonePeer,
): Promise<void> {
    await driveToPending(link, transport, phone);
    await link.receive(await phone.prepareFrame());
    await link.receive(await phone.completeFrame());
}

describe('revocation integration: store failures fail closed', () => {
    it('closes unrelated sessions when the reconnect lookup reads an invalid identity', async () => {
        const stack = await createStack();
        const first = await TestPhone.create();
        const second = await TestPhone.create();
        const firstPeer = await addPeer(stack, first);
        const secondPeer = await addPeer(stack, second);
        await openSession(
            stack,
            { phone: first, peerId: firstPeer },
            HANDLE_CONNECTION,
            HANDLE_DEVICE,
        );
        await openSession(
            stack,
            { phone: second, peerId: secondPeer },
            HANDLE_CONNECTION_2,
            HANDLE_DEVICE_2,
        );
        expect(stack.manager.authenticatedSessions()).toHaveLength(2);
        const before = structuredClone(stack.local.snapshot);

        // The identity record becomes invalid: the next repository transaction is a store failure.
        stack.keys.failure = new TrustError();
        const third = await TestPhone.create();
        await connectForSession(stack.controller, HANDSHAKE_HANDLE, HANDSHAKE_DEVICE);
        const opened = stack.manager.open(HANDSHAKE_HANDLE, hexOf(third.installationId));
        const port = stack.controller.currentPort();
        const hello = await nextSend(port);
        await ackSend(port, hello);
        const side = new PhoneSide(third);
        await emitRecord(
            port,
            HANDSHAKE_HANDLE,
            await side.acceptClientHello(decodeFrame(hello.bytes).payload),
        );
        await flush();

        await expect(opened).resolves.toEqual({ result: 'failed' });
        expect(stack.statuses).toEqual(['re-pair-required']);
        expect(terminations(stack, HANDLE_CONNECTION)).toEqual(['closed']);
        expect(terminations(stack, HANDLE_CONNECTION_2)).toEqual(['closed']);
        expect(stack.manager.authenticatedSessions()).toEqual([]);
        // Fail-closed deletes nothing: the durable state is byte-identical.
        expect(stack.local.snapshot).toEqual(before);
    });

    it('fails closed when the removal handler durable delete fails in a live session', async () => {
        const stack = await createStack();
        const first = await TestPhone.create();
        const second = await TestPhone.create();
        const firstPeer = await addPeer(stack, first);
        const secondPeer = await addPeer(stack, second);
        // Production composition: the real activation handler plus the real removal handler,
        // whose `deleteFenced` is the real coordinator over the real repository.
        stack.handler = composeHandlers(
            new ActivationEngine({ trust: stack.repository, proofs: stack.proofs }),
            createRemovalHandler(stack.coordinator),
        );
        const side = await openSession(stack, { phone: first, peerId: firstPeer });
        await openSession(
            stack,
            { phone: second, peerId: secondPeer },
            HANDLE_CONNECTION_2,
            HANDLE_DEVICE_2,
        );
        expect(stack.manager.authenticatedSessions()).toHaveLength(2);
        const port = stack.controller.currentPort();

        // The handler's durable delete is the next write, and it fails: the real repository
        // reports the episode, the real callback runs the fail-closed action and the real
        // coordinator still owns the fence.
        stack.local.writeFaultAt = stack.local.writeCount + 1;
        await emitRecord(
            port,
            HANDLE_CONNECTION,
            await side.applicationFrame(RecordType.REMOVAL_REQUEST, 1n),
        );
        await flush();

        expect(postedTypes(port, HANDLE_CONNECTION)).not.toContain(RecordType.REMOVAL_RESPONSE);
        expect(stack.statuses).toEqual(['re-pair-required']);
        expect(stack.manager.authenticatedSessions()).toEqual([]);
        expect(terminations(stack, HANDLE_CONNECTION)).toHaveLength(1);
        expect(terminations(stack, HANDLE_CONNECTION_2)).toHaveLength(1);
        expect(stack.local.clearCalls).toBe(0);
        expect((await stack.repository.peers()).map((peer) => peer.installationId)).toContain(
            secondPeer,
        );

        // The fence was released after the failed handler: a deliberate pairing of the same id
        // can write a fresh pending record.
        await stack.repository.revoke(firstPeer);
        await stack.repository.addPending({
            installationId: firstPeer,
            publicKey: hex(first.publicKey),
            label: 'Pixel test phone',
            deviceType: 0,
            enrollmentTranscript: hex(ET),
        });
        expect((await stack.repository.peers()).map((peer) => peer.installationId)).toContain(
            firstPeer,
        );
    });

    it('closes unrelated sessions when the durable authentication write fails', async () => {
        const stack = await createStack();
        const first = await TestPhone.create();
        const second = await TestPhone.create();
        const third = await TestPhone.create();
        const firstPeer = await addPeer(stack, first);
        const secondPeer = await addPeer(stack, second);
        const thirdPeer = await addPeer(stack, third);
        await openSession(
            stack,
            { phone: first, peerId: firstPeer },
            HANDLE_CONNECTION,
            HANDLE_DEVICE,
        );
        await openSession(
            stack,
            { phone: second, peerId: secondPeer },
            HANDLE_CONNECTION_2,
            HANDLE_DEVICE_2,
        );

        // Every handshake read succeeds and only the authentication metadata write fails: the
        // fault is armed after the confirm exchange, when that write is the only one left.
        await connectForSession(stack.controller, HANDSHAKE_HANDLE, HANDSHAKE_DEVICE);
        const opened = stack.manager.open(HANDSHAKE_HANDLE, thirdPeer);
        await answerHandshake(stack.controller, {
            phone: third,
            connectionHandle: HANDSHAKE_HANDLE,
            beforeConfirm: async () => {
                stack.local.writeFaultAt = stack.local.writeCount + 1;
            },
        });
        await flush();

        await expect(opened).resolves.toEqual({ result: 'failed' });
        expect(stack.statuses).toEqual(['re-pair-required']);
        expect(terminations(stack, HANDLE_CONNECTION)).toEqual(['closed']);
        expect(terminations(stack, HANDLE_CONNECTION_2)).toEqual(['closed']);
        expect(stack.manager.authenticatedSessions()).toEqual([]);
    });
});

describe('revocation integration: revoke races an enrollment', () => {
    it('refuses the pending write when the peer is revoked before addPending', async () => {
        const stack = await createStack();
        const phone = await PhonePeer.create({ code: CODE });
        const gate = deferred();
        const entered = deferred();
        const gated: PairingLinkRepository = {
            identity: () => stack.repository.identity(),
            peers: () => stack.repository.peers(),
            addPending: async (input) => {
                entered.resolve();
                await gate.promise;
                return stack.repository.addPending(input);
            },
            confirm: (...args) => stack.repository.confirm(...args),
            revoke: (peerId) => stack.repository.revoke(peerId),
            revokePending: (peerId) => stack.repository.revokePending(peerId),
        };
        const { link, transport } = await startLink(stack, { repo: gated });
        const peerId = hexOf(phone.installationId);
        try {
            await link.receive(phone.offerFor(transport.sent[0]));
            const cA = phone.acceptPoint(transport.sent[1]);
            await link.receive(encodePairingFrame(PairingFrameType.CA, cA));
            await phone.acceptConfirmB(transport.sent[2]);
            // The phone identity authenticates and binds the link before the durable write.
            const persist = link.receive(await phone.identityFrame());
            await entered.promise;
            expect(link.boundPeerId()).toBe(peerId);

            const revocation = stack.coordinator.revoke(peerId);
            await microFlush();
            // The delete happened; the termination waits behind the link's in-flight frame.
            expect(await stack.repository.peers()).toEqual([]);
            expect(transport.disconnectCount).toBe(0);

            gate.resolve();
            await persist;
            await revocation;

            // The fenced persist is a peer rejection, never a store failure.
            expect(stack.statuses).toEqual([]);
            expect(await stack.repository.peers()).toEqual([]);
            // Both the abort's terminate and the failed machine end the same handle.
            expect(transport.disconnectCount).toBeGreaterThanOrEqual(1);
            // The enrollment never released its identity record or a prepare.
            expect(transport.sent).toHaveLength(3);
            expect(link.machine.state).toBe('idle');
        } finally {
            phone.close();
        }
    });

    it('fails a later activation step after a revoke between persist and commit', async () => {
        const stack = await createStack();
        const phone = await PhonePeer.create({ code: CODE });
        const { link, transport } = await startLink(stack);
        const peerId = hexOf(phone.installationId);
        try {
            await driveToPending(link, transport, phone);
            expect((await stack.repository.peers())[0]?.state).toBe('pending');

            await stack.coordinator.revoke(peerId);

            expect(await stack.repository.peers()).toEqual([]);
            expect(transport.disconnectCount).toBe(1);
            expect(stack.statuses).toEqual([]);
            // A later activation step reaches no live connection.
            await link.receive(await phone.prepareFrame());
            expect(transport.sent).toHaveLength(4);
        } finally {
            phone.close();
        }
    });

    it('fails a later acknowledgement after a revoke between commit and ack', async () => {
        const stack = await createStack();
        const phone = await PhonePeer.create({ code: CODE });
        const { link, transport } = await startLink(stack);
        const peerId = hexOf(phone.installationId);
        try {
            await driveToPending(link, transport, phone);
            await link.receive(await phone.prepareFrame());
            // The client's commit is out; the record moves only on activation_complete.
            expect((await stack.repository.peers())[0]?.state).toBe('pending');
            expect(link.machine.state).toBe('awaiting_complete');
            expect(transport.sent).toHaveLength(5);

            await stack.coordinator.revoke(peerId);

            expect(await stack.repository.peers()).toEqual([]);
            expect(transport.disconnectCount).toBe(1);
            await link.receive(await phone.completeFrame());
            expect(transport.sent).toHaveLength(5);
        } finally {
            phone.close();
        }
    });

    it('terminates a link aborted mid-frame without deleting its pending record', async () => {
        const stack = await createStack();
        const phone = await PhonePeer.create({ code: CODE });
        let aborted = false;
        const { link, transport } = await startLink(stack, { isAborted: () => aborted });
        const peerId = hexOf(phone.installationId);
        try {
            await driveToPending(link, transport, phone);
            expect((await stack.repository.peers())[0]?.state).toBe('pending');

            aborted = true;
            // A record that arrives after the abort is not processed: the link is terminated
            // and the durable pending record survives, unlike a cancellation.
            await link.receive(await phone.prepareFrame());
            await link.terminate();

            expect(transport.disconnectCount).toBe(1);
            expect(transport.sent).toHaveLength(4);
            expect(link.machine.state).toBe('idle');
            const peer = (await stack.repository.peers()).find(
                (candidate) => candidate.installationId === peerId,
            );
            expect(peer?.state).toBe('pending');
        } finally {
            phone.close();
        }
    });

    it('closes a signed-recovery session after a revoke', async () => {
        const stack = await createStack();
        const phone = await TestPhone.create();
        const peerId = await addPeer(stack, phone, { state: 'pending', et: ET });
        stack.handler = composeHandlers(
            new ActivationEngine({ trust: stack.repository, proofs: stack.proofs }),
            createRemovalHandler(stack.coordinator),
        );
        const side = await openSession(
            stack,
            { phone, peerId },
            HANDLE_CONNECTION,
            HANDLE_DEVICE,
            ET,
        );
        expect(stack.manager.authenticatedSessions()[0]?.sessionClass).toBeTruthy();
        expect(side.keys.recovery).not.toBeNull();

        await stack.coordinator.revoke(peerId);

        expect(await stack.repository.peers()).toEqual([]);
        expect(stack.manager.authenticatedSessions()).toEqual([]);
        expect(terminations(stack, HANDLE_CONNECTION)).toEqual(['revoked']);
        expect(stack.statuses).toEqual([]);
    });

    it('keeps the fence across a duplicate activation complete and leaves no record', async () => {
        const stack = await createStack();
        const phone = await TestPhone.create();
        const peerId = await addPeer(stack, phone, { state: 'pending', et: ET });
        const gate = deferred();
        const entered = deferred();
        let confirms = 0;
        stack.handler = composeHandlers(
            new ActivationEngine({
                trust: {
                    peers: () => stack.repository.peers(),
                    revoke: (id) => stack.repository.revoke(id),
                    confirm: async (id, stage, proof) => {
                        confirms += 1;
                        // The first completed activation is synchronous; the duplicate is held
                        // inside its store write so the revocation runs concurrently with it.
                        if (confirms > 1) {
                            entered.resolve();
                            await gate.promise;
                        }
                        return stack.repository.confirm(id, stage, proof);
                    },
                },
                proofs: stack.proofs,
            }),
            createRemovalHandler(stack.coordinator),
        );
        const side = await openSession(
            stack,
            { phone, peerId },
            HANDLE_CONNECTION,
            HANDLE_DEVICE,
            ET,
        );
        const port = stack.controller.currentPort();
        const recovery = requireValue(side.keys.recovery);
        const phoneMac = async (stage: number): Promise<Uint8Array> =>
            hmacSha256(recovery.kRecPhone, concat(ET, Uint8Array.of(stage)));

        await emitRecord(
            port,
            HANDLE_CONNECTION,
            await side.applicationFrame(
                RecordType.ENROLLMENT_PREPARE,
                1n,
                await phoneMac(STAGE_PREPARE),
            ),
        );
        // The handler answered with this session's activation_commit.
        const commit = await nextSend(port);
        expect(postedTypes(port, HANDLE_CONNECTION)).toContain(RecordType.ACTIVATION_COMMIT);
        await ackSend(port, commit);

        // The first activation complete finishes: the record moves and the session stays open.
        await emitRecord(
            port,
            HANDLE_CONNECTION,
            await side.applicationFrame(
                RecordType.ACTIVATION_COMPLETE,
                2n,
                await phoneMac(STAGE_COMPLETE),
            ),
        );
        const ack = await nextSend(port);
        await ackSend(port, ack);
        const activated = (await stack.repository.peers()).find(
            (peer) => peer.installationId === peerId,
        );
        expect(activated?.state).toBe('active_activation_pending');
        expect(stack.manager.authenticatedSessions()).toHaveLength(1);

        // A duplicate activation_complete of the same authenticated record races the revocation.
        const duplicate = emitRecord(
            port,
            HANDLE_CONNECTION,
            await side.applicationFrame(
                RecordType.ACTIVATION_COMPLETE,
                3n,
                await phoneMac(STAGE_COMPLETE),
            ),
        );
        await entered.promise;
        const revocation = stack.coordinator.revoke(peerId);
        await microFlush();
        gate.resolve();
        await duplicate;
        await revocation;
        await flush();

        expect(await stack.repository.peers()).toEqual([]);
        expect(stack.manager.authenticatedSessions()).toEqual([]);
        // The revoke won the race: the in-flight duplicate confirm was a peer rejection, not a
        // store failure, and the engine's own failure path ended the generation.
        expect(stack.statuses).toEqual([]);
        expect(terminations(stack, HANDLE_CONNECTION)).toHaveLength(1);

        // The fence was released after the sweep: a deliberate pairing of the same id can write.
        await stack.repository.addPending({
            installationId: peerId,
            publicKey: hex(phone.publicKey),
            label: 'Pixel test phone',
            deviceType: 0,
            enrollmentTranscript: hex(ET),
        });
        expect((await stack.repository.peers()).map((peer) => peer.installationId)).toEqual([
            peerId,
        ]);
    });
});

describe('revocation integration: revoked peers stay out', () => {
    it('fails a revoked peer reconnect generically and leaves other sessions alone', async () => {
        const stack = await createStack();
        const revoked = await TestPhone.create();
        const other = await TestPhone.create();
        const revokedPeer = await addPeer(stack, revoked);
        const otherPeer = await addPeer(stack, other);
        await openSession(
            stack,
            { phone: revoked, peerId: revokedPeer },
            HANDLE_CONNECTION,
            HANDLE_DEVICE,
        );
        await openSession(
            stack,
            { phone: other, peerId: otherPeer },
            HANDLE_CONNECTION_2,
            HANDLE_DEVICE_2,
        );

        await stack.coordinator.revoke(revokedPeer);

        expect(terminations(stack, HANDLE_CONNECTION)).toEqual(['revoked']);
        expect(terminations(stack, HANDLE_CONNECTION_2)).toEqual([]);
        expect(stack.manager.authenticatedSessions()).toHaveLength(1);

        // The offline-while-revoked counterpart reconnects: one generic pre-auth refusal.
        const port = stack.controller.currentPort();
        // Answer the disconnect the revocation posted, so the next request is the real one.
        await ackDisconnect(port, HANDLE_CONNECTION);
        await connectForSession(stack.controller, HANDSHAKE_HANDLE, HANDSHAKE_DEVICE);
        const opened = stack.manager.open(HANDSHAKE_HANDLE, revokedPeer);
        const hello = await nextSend(port);
        await ackSend(port, hello);
        const side = new PhoneSide(revoked);
        await emitRecord(
            port,
            HANDSHAKE_HANDLE,
            await side.acceptClientHello(decodeFrame(hello.bytes).payload),
        );
        await flush();

        await expect(opened).resolves.toEqual({ result: 'failed' });
        const refused = port.posted.filter(
            (message) =>
                (message as { type?: string; connection_handle?: string }).type === 'send' &&
                (message as { connection_handle?: string }).connection_handle === HANDSHAKE_HANDLE,
        );
        expect(refused).toHaveLength(1);
        expect(terminations(stack, HANDSHAKE_HANDLE)).toEqual(['closed_wire']);
        expect(stack.statuses).toEqual([]);
        expect(stack.manager.authenticatedSessions()).toHaveLength(1);
    });

    it('re-pairs after a revoke with matching fingerprints', async () => {
        const stack = await createStack();
        const phone = await PhonePeer.create({ code: CODE });
        const peerId = hexOf(phone.installationId);
        const first = await startLink(stack, { deviceHandle: 'device-first' });
        try {
            await driveToActive(first.link, first.transport, phone);
            const original = (await stack.repository.peers()).find(
                (peer) => peer.installationId === peerId,
            );
            // The record moves to active only on a later phone-active reconnect.
            expect(original?.state).toBe('active_activation_pending');
            const displayed = first.link.machine.fingerprint();
            expect(displayed).toBe(
                fingerprintDisplay(bytes(requireValue(original).fingerprint, 32)),
            );

            await stack.coordinator.revoke(peerId);
            expect(await stack.repository.peers()).toEqual([]);

            // The fence was released: a deliberate re-pairing of the same id succeeds.
            const second = await startLink(stack, { deviceHandle: 'device-second' });
            await driveToActive(second.link, second.transport, phone);
            const repaired = (await stack.repository.peers()).find(
                (peer) => peer.installationId === peerId,
            );
            expect(repaired?.state).toBe('active_activation_pending');
            // The same identities define the same fingerprint on the new record.
            expect(repaired?.fingerprint).toBe(original?.fingerprint);
            expect(second.link.machine.fingerprint()).toBe(
                fingerprintDisplay(bytes(requireValue(repaired).fingerprint, 32)),
            );
        } finally {
            phone.close();
        }
    });

    it('refuses a pairing while a record is held', async () => {
        const stack = await createStack();
        const phone = await PhonePeer.create({ code: CODE });
        const existing = await addPeer(stack, {
            installationId: phone.installationId,
            publicKey: phone.identityPubkey,
        });
        const before = (await stack.repository.peers()).find(
            (peer) => peer.installationId === existing,
        );
        const { link, transport } = await startLink(stack);
        try {
            await link.receive(phone.offerFor(transport.sent[0]));
            const cA = phone.acceptPoint(transport.sent[1]);
            await link.receive(encodePairingFrame(PairingFrameType.CA, cA));
            await phone.acceptConfirmB(transport.sent[2]);
            await link.receive(await phone.identityFrame());

            // The duplicate installation id is refused: no record is replaced, no identity released.
            expect(transport.sent).toHaveLength(3);
            expect(link.machine.failed).toBe(true);
            expect(link.machine.state).toBe('idle');
            const after = await stack.repository.peers();
            expect(after).toHaveLength(1);
            expect(after[0]).toEqual(before);
        } finally {
            phone.close();
        }
    });

    it('removes the platform hint with the peer and rejects a later hint write', async () => {
        const stack = await createStack();
        const phone = await TestPhone.create();
        const peerId = await addPeer(stack, phone, { hint: 'winrt:phone-a' });
        expect((await stack.repository.peers())[0]?.platformHint).toBe('winrt:phone-a');

        await stack.coordinator.revoke(peerId);

        expect(await stack.repository.peers()).toEqual([]);
        await expect(
            stack.repository.authenticated(peerId, 'winrt:phone-a', new Uint8Array(32)),
        ).rejects.toBeInstanceOf(TrustError);
        expect(stack.statuses).toEqual([]);
    });
});

describe('revocation integration: peer listing', () => {
    it('lists every peer fresh without secrets or hints', async () => {
        const stack = await createStack();
        const active = await TestPhone.create();
        const pending = await TestPhone.create();
        const activePeer = await addPeer(stack, active, { hint: 'winrt:phone-a', label: 'Pixel' });
        const pendingPeer = await addPeer(stack, pending, { state: 'pending' });
        await openSession(stack, { phone: active, peerId: activePeer });

        const summaries = await stack.coordinator.peers();

        expect(summaries).toHaveLength(2);
        const listed = summaries.find((summary) => summary.installationId === activePeer);
        expect(listed?.session).toBe('full');
        expect(listed?.label).toBe('Pixel');
        expect(listed?.deviceType).toBe(0);
        expect(listed?.state).toBe('active');
        expect(listed?.createdAtMillis).toBe(1_000);
        expect(listed?.lastSeenMillis).toBe(1_000);
        expect(listed?.fingerprint).toMatch(/^[0-9a-f]{64}$/);
        const other = summaries.find((summary) => summary.installationId === pendingPeer);
        expect(other?.session).toBe('none');
        expect(other?.state).toBe('pending');

        const stored = (await stack.repository.peers()).find(
            (peer) => peer.installationId === activePeer,
        );
        const serialized = JSON.stringify(summaries);
        expect(serialized).not.toContain(requireValue(stored).publicKey);
        expect(serialized).not.toContain('winrt:phone-a');
        expect(serialized).not.toContain('platformHint');
        expect(serialized).not.toContain('enrollmentTranscript');
        expect(serialized).not.toContain('pendingContext');
        expect(serialized).not.toContain('handle');
    });
});

/**
 * The device shape of the OTP-11 run: a generation's class is fixed at its authentication gate, so
 * a recovery-only generation can never carry a removal record — even after its activation exchange
 * made the phone `ACTIVE`. The peer summary must report that class instead of a bare "connected",
 * and only a new generation can reach mutual removal (OTP2-REC-013 needs `FULL`).
 */
describe('revocation integration: recovery-only sessions', () => {
    /** Answers the unprompted `activation_ack` a below-active recovery generation owes. */
    async function acknowledgeActivation(stack: Stack, handle: string): Promise<void> {
        const port = stack.controller.currentPort();
        const ack = await nextSend(port);
        expect(postedTypes(port, handle).at(-1)).toBe(RecordType.ACTIVATION_ACK);
        await ackSend(port, ack);
    }

    function summaryKind(
        summaries: readonly { installationId: string; session: string }[],
        peerId: string,
    ): string | undefined {
        return summaries.find((summary) => summary.installationId === peerId)?.session;
    }

    /** The production handler composition: activation (the unprompted ack) plus removal. */
    async function recoveryStack(): Promise<Stack> {
        const stack = await createStack();
        stack.handler = composeHandlers(
            new ActivationEngine({ trust: stack.repository, proofs: stack.proofs }),
            createRemovalHandler(stack.coordinator),
        );
        return stack;
    }

    it('keeps the record and reports recovery for an activation-recovery generation', async () => {
        const stack = await recoveryStack();
        const phone = await TestPhone.create();
        // The interrupted activation left the client mirror below active.
        const peerId = await addPeer(stack, phone, { state: 'active_activation_pending' });
        await openSession(
            stack,
            { phone, peerId },
            HANDLE_CONNECTION,
            HANDLE_DEVICE,
            undefined,
            PEER_STATE_BELOW_ACTIVE,
        );
        await acknowledgeActivation(stack, HANDLE_CONNECTION);
        expect(stack.manager.authenticatedSessions()[0]?.sessionClass).toBe(
            SessionClass.ACTIVATION_RECOVERY,
        );
        expect(summaryKind(await stack.coordinator.peers(), peerId)).toBe('recovery');

        const before = structuredClone(stack.local.snapshot);
        await expect(stack.coordinator.removeMutually(peerId)).resolves.toBe('kept-not-connected');

        // Nothing was sent and nothing was deleted: the generation is still live and keeps its
        // durable record, and the recovery kind is why the surface must offer a reconnect.
        expect(postedTypes(stack.controller.currentPort(), HANDLE_CONNECTION)).not.toContain(
            RecordType.REMOVAL_REQUEST,
        );
        expect(stack.local.snapshot).toEqual(before);
        expect(stack.manager.authenticatedSessions()).toHaveLength(1);
        expect(terminations(stack, HANDLE_CONNECTION)).toEqual([]);
    });

    it('reports a full-pending-peer generation as recovery and refuses removal', async () => {
        const stack = await recoveryStack();
        const phone = await TestPhone.create();
        const peerId = await addPeer(stack, phone, { state: 'active' });
        // The phone's record is active but it does not report itself active: FULL_PENDING_PEER.
        await openSession(
            stack,
            { phone, peerId },
            HANDLE_CONNECTION,
            HANDLE_DEVICE,
            undefined,
            PEER_STATE_BELOW_ACTIVE,
        );
        await acknowledgeActivation(stack, HANDLE_CONNECTION);
        expect(stack.manager.authenticatedSessions()[0]?.sessionClass).toBe(
            SessionClass.FULL_PENDING_PEER,
        );
        expect(summaryKind(await stack.coordinator.peers(), peerId)).toBe('recovery');

        const before = structuredClone(stack.local.snapshot);
        await expect(stack.coordinator.removeMutually(peerId)).resolves.toBe('kept-not-connected');

        expect(postedTypes(stack.controller.currentPort(), HANDLE_CONNECTION)).not.toContain(
            RecordType.REMOVAL_REQUEST,
        );
        expect(stack.local.snapshot).toEqual(before);
        expect(stack.manager.authenticatedSessions()).toHaveLength(1);
    });

    it('reads full and removes mutually after the recovery generation is replaced', async () => {
        const stack = await recoveryStack();
        const phone = await TestPhone.create();
        // The device run's durable state: the phone's activation moved its record to active, the
        // client's mirror stayed at `active_activation_pending` because its ack never arrived.
        const peerId = await addPeer(stack, phone, { state: 'active_activation_pending' });
        const port = stack.controller.currentPort();

        // Generation 1: a recovery-only generation; its class never changes after its gate, so
        // mutual removal cannot be asked on it even though the phone record is now active.
        await openSession(
            stack,
            { phone, peerId },
            HANDLE_CONNECTION,
            HANDLE_DEVICE,
            undefined,
            PEER_STATE_BELOW_ACTIVE,
        );
        await acknowledgeActivation(stack, HANDLE_CONNECTION);
        expect(stack.manager.authenticatedSessions()[0]?.sessionClass).toBe(
            SessionClass.ACTIVATION_RECOVERY,
        );
        expect(summaryKind(await stack.coordinator.peers(), peerId)).toBe('recovery');

        // The operator disconnects it: the recovery generation ends and the device is free.
        stack.manager.close(HANDLE_CONNECTION);
        await ackDisconnect(port, HANDLE_CONNECTION);
        await flush();
        expect(stack.manager.authenticatedSessions()).toEqual([]);
        expect(summaryKind(await stack.coordinator.peers(), peerId)).toBe('none');

        // Generation 2: the phone reports itself active (`0x01`), which promotes the mirror to
        // `active` and fixes the class at FULL.
        const second = await openSession(
            stack,
            { phone, peerId },
            HANDSHAKE_HANDLE,
            HANDSHAKE_DEVICE,
        );
        expect(stack.manager.authenticatedSessions()[0]?.sessionClass).toBe(SessionClass.FULL);
        const record = (await stack.repository.peers()).find(
            (peer) => peer.installationId === peerId,
        );
        expect(record?.state).toBe('active');
        expect(summaryKind(await stack.coordinator.peers(), peerId)).toBe('full');

        // Mutual removal now has the FULL session it requires, and it succeeds.
        const removal = stack.coordinator.removeMutually(peerId);
        const request = await nextSend(port);
        const requestRecord = await second.openClientRecord(request.bytes, 1n);
        expect(requestRecord.type).toBe(RecordType.REMOVAL_REQUEST);
        await ackSend(port, request);
        await emitRecord(
            port,
            HANDSHAKE_HANDLE,
            await second.applicationFrame(RecordType.REMOVAL_RESPONSE, 1n),
        );

        await expect(removal).resolves.toBe('removed');
        expect(await stack.repository.peers()).toEqual([]);
        expect(terminations(stack, HANDSHAKE_HANDLE)).toEqual(['removal']);
        expect(stack.manager.authenticatedSessions()).toEqual([]);
        // The record is gone, so the peer leaves the listing entirely.
        expect(await stack.coordinator.peers()).toEqual([]);
    });
});

class SilentView implements ConnectorView {
    render(): void {
        // The wiring tests assert on the store, the sessions and the posted requests.
    }
    onAction(): () => void {
        return () => undefined;
    }
}

interface AppFixture {
    app: ConnectorApp;
    controller: TestController;
    local: MemoryLocal;
    keys: TestKeys;
    proofs: ProofRegistry;
    repository: ChromeTrustRepository | null;
    usedIdentities: Uint8Array[];
}

/** The production wiring: one repository, one manager, the removal handler and the cache. */
function createAppFixture(): AppFixture {
    const controller = createTestController();
    const local = new MemoryLocal();
    const keys = new TestKeys();
    const proofs = new ProofRegistry();
    const usedIdentities: Uint8Array[] = [];
    const fixture: AppFixture = {
        app: undefined as unknown as ConnectorApp,
        controller,
        local,
        keys,
        proofs,
        repository: null,
        usedIdentities,
    };
    const app = new ConnectorApp({
        controller: controller.controller,
        view: new SilentView(),
        setTimeout: controller.clock.setTimeout,
        clearTimeout: controller.clock.clearTimeout,
        session: {
            createRepository(verifier, _proofs, onStoreFailure) {
                const repository = new ChromeTrustRepository(
                    keys,
                    local,
                    lock(),
                    verifier,
                    controller.clock.now,
                    () => 1_000,
                    { id: OWNER_ID, isAlive: async () => true },
                    onStoreFailure,
                );
                fixture.repository = repository;
                return repository;
            },
            createEngineDeps(_handle, peerId, localInstallationId, repository, appProofs) {
                usedIdentities.push(localInstallationId.slice());
                return {
                    localInstallationId,
                    expectedPeerId: peerId,
                    isCurrent: () => true,
                    lookupPhone: async (id) => {
                        const peers = await repository.peers();
                        const peer = peers.find((candidate) => candidate.installationId === id);
                        if (peer === undefined) return undefined;
                        return {
                            publicKey: bytes(peer.publicKey, 65),
                            state: peer.state,
                            et: bytes(peer.enrollmentTranscript, 32),
                        };
                    },
                    upgradeMirror: (id, proof) => repository.confirm(id, 'phone_active', proof),
                    now: controller.clock.now,
                    sign: async (message) => {
                        const stored = requireValue(keys.value);
                        return normalizeLowS(
                            new Uint8Array(
                                await crypto.subtle.sign(
                                    { name: 'ECDSA', hash: 'SHA-256' },
                                    stored.privateKey,
                                    message,
                                ),
                            ),
                        );
                    },
                    handler: new ActivationEngine({ trust: repository, proofs: appProofs }),
                };
            },
        },
    });
    fixture.app = app;
    return fixture;
}

function requireRepository(fixture: AppFixture): ChromeTrustRepository {
    return requireValue(fixture.repository);
}

async function startApp(fixture: AppFixture): Promise<void> {
    const started = fixture.app.start();
    const posted = await fixture.controller.currentPort().nextPosted();
    await fixture.controller
        .currentPort()
        .emit(readyEvent({ request_id: posted.request_id as string }));
    await started;
    await microFlush();
}

async function addAppPeer(
    fixture: AppFixture,
    phone: TestPhone,
    options: { state?: TrustState; hint?: string } = {},
): Promise<string> {
    // The app loads the identity lazily; a seeded record needs the store's own identity first.
    await requireRepository(fixture).identity();
    return addPeer(
        { keys: fixture.keys, local: fixture.local, clock: fixture.controller.clock },
        phone,
        options,
    );
}

async function connectThroughApp(
    fixture: AppFixture,
    phone: TestPhone,
    peerId: string,
    handle: string = HANDLE_CONNECTION,
    deviceHandle: string = HANDLE_DEVICE,
): Promise<void> {
    const port = fixture.controller.currentPort();
    const action = fixture.app.handleAction({ type: 'connect', deviceHandle, peerId });
    const posted = await port.nextPosted();
    await port.emit(
        connectedEvent({
            request_id: posted.request_id as string,
            connection_handle: handle,
        }),
    );
    await answerHandshake(fixture.controller, { phone, connectionHandle: handle });
    await action;
    await flush();
}

/** Drives one full enrollment through the connector's own pairing flow and port. */
async function pairThroughApp(
    fixture: AppFixture,
    phone: PhonePeer,
    deviceHandle: string,
): Promise<void> {
    const port = fixture.controller.currentPort();
    const pairing = fixture.app.startPairing(deviceHandle, CODE);
    const connect = await port.nextPosted();
    await port.emit(
        connectedEvent({
            request_id: connect.request_id as string,
            connection_handle: PAIR_HANDLE,
        }),
    );
    const request = await nextSend(port);
    await ackSend(port, request);
    await emitRecord(port, PAIR_HANDLE, phone.offerFor(request.bytes));

    const point = await nextSend(port);
    await ackSend(port, point);
    await emitRecord(
        port,
        PAIR_HANDLE,
        encodePairingFrame(PairingFrameType.CA, phone.acceptPoint(point.bytes)),
    );

    const confirm = await nextSend(port);
    await ackSend(port, confirm);
    await phone.acceptConfirmB(confirm.bytes);

    await emitRecord(port, PAIR_HANDLE, await phone.identityFrame());
    const identity = await nextSend(port);
    await ackSend(port, identity);
    await phone.acceptClientIdentity(identity.bytes);

    await emitRecord(port, PAIR_HANDLE, await phone.prepareFrame());
    const commit = await nextSend(port);
    await ackSend(port, commit);

    await emitRecord(port, PAIR_HANDLE, await phone.completeFrame());
    const ack = await nextSend(port);
    await ackSend(port, ack);
    await expect(pairing).resolves.toBe(true);
    // The activation end posts the link's terminal disconnect; answer it so the next
    // request this test issues is the real one.
    await ackDisconnect(port, PAIR_HANDLE);
    await flush();
}

describe('revocation integration: connector wiring', () => {
    it('aborts a startup whose pairing connect is pending when fail-closed runs', async () => {
        const fixture = createAppFixture();
        await startApp(fixture);
        const repository = requireRepository(fixture);
        const existing = await addAppPeer(fixture, await TestPhone.create(), { state: 'pending' });

        const pairing = fixture.app.startPairing(PAIR_DEVICE, CODE);
        await microFlush();
        const port = fixture.controller.currentPort();
        const connect = await port.nextPosted();
        expect(connect.type).toBe('connect');

        // A store failure observed elsewhere escalates to the fail-closed action.
        fixture.local.readFault = new TrustError();
        await expect(repository.peers()).rejects.toThrow('re-pair');
        await microFlush();
        expect(fixture.app.trustStatus).toBe('re-pair-required');

        await port.emit(
            connectedEvent({
                request_id: connect.request_id as string,
                connection_handle: PAIR_HANDLE,
            }),
        );
        // The non-destructive teardown posts exactly one disconnect for the startup's handle.
        await ackDisconnect(port, PAIR_HANDLE);
        await expect(pairing).resolves.toBe(false);
        await flush();

        // The aborted startup sent nothing and released the handle without deleting anything.
        expect(postsOf(port, 'send')).toHaveLength(0);
        expect(
            postsOf(port, 'disconnect').filter(
                (message) => message.connection_handle === PAIR_HANDLE,
            ),
        ).toHaveLength(1);
        expect(fixture.app.pairingFinished).toBe(true);
        expect(fixture.app.pairingState).toEqual({ phase: 'idle', finished: true });
        fixture.local.readFault = null;
        expect((await repository.peers()).map((peer) => peer.installationId)).toEqual([existing]);
    });

    it('aborts a startup whose initial send is pending when reset runs', async () => {
        const fixture = createAppFixture();
        await startApp(fixture);
        const repository = requireRepository(fixture);
        const port = fixture.controller.currentPort();

        const pairing = fixture.app.startPairing(PAIR_DEVICE, CODE);
        const connect = await port.nextPosted();
        await port.emit(
            connectedEvent({
                request_id: connect.request_id as string,
                connection_handle: PAIR_HANDLE,
            }),
        );
        const request = await nextSend(port);

        await fixture.app.resetTrust();

        // The send resolves after the reset: no link is installed and no record is written late.
        await ackSend(port, request);
        await ackDisconnect(port, PAIR_HANDLE);
        await expect(pairing).resolves.toBe(false);
        await flush();

        expect(postsOf(port, 'send')).toHaveLength(1);
        expect(
            postsOf(port, 'disconnect').filter(
                (message) => message.connection_handle === PAIR_HANDLE,
            ),
        ).toHaveLength(1);
        expect(await repository.peers()).toEqual([]);
        expect(fixture.local.snapshot?.peers).toEqual([]);
    });

    it('aborts a startup whose initial send is pending when fail-closed runs', async () => {
        const fixture = createAppFixture();
        await startApp(fixture);
        const repository = requireRepository(fixture);
        const existing = await addAppPeer(fixture, await TestPhone.create(), { state: 'pending' });
        const port = fixture.controller.currentPort();

        const pairing = fixture.app.startPairing(PAIR_DEVICE, CODE);
        const connect = await port.nextPosted();
        await port.emit(
            connectedEvent({
                request_id: connect.request_id as string,
                connection_handle: PAIR_HANDLE,
            }),
        );
        // The initial `pairing_request` is out and unanswered: the startup is still in `start`.
        const request = await nextSend(port);

        // A store failure observed elsewhere escalates to the fail-closed action.
        fixture.local.readFault = new TrustError();
        await expect(repository.peers()).rejects.toThrow('re-pair');
        await microFlush();
        expect(fixture.app.trustStatus).toBe('re-pair-required');

        // The send resolves after the abort: no link is installed and no record is written late.
        await ackSend(port, request);
        await ackDisconnect(port, PAIR_HANDLE);
        await expect(pairing).resolves.toBe(false);
        await flush();

        expect(postsOf(port, 'send')).toHaveLength(1);
        expect(
            postsOf(port, 'disconnect').filter(
                (message) => message.connection_handle === PAIR_HANDLE,
            ),
        ).toHaveLength(1);
        expect(fixture.app.pairingState).toEqual({ phase: 'idle', finished: true });
        // Fail-closed deleted nothing: the durable pending record survives and no record was added.
        fixture.local.readFault = null;
        expect((await repository.peers()).map((peer) => peer.installationId)).toEqual([existing]);
    });

    it('aborts a startup whose pairing connect is pending when reset runs', async () => {
        const fixture = createAppFixture();
        await startApp(fixture);
        const repository = requireRepository(fixture);
        const port = fixture.controller.currentPort();

        const pairing = fixture.app.startPairing(PAIR_DEVICE, CODE);
        await microFlush();
        const connect = await port.nextPosted();
        expect(connect.type).toBe('connect');

        await fixture.app.resetTrust();

        // The connect resolves after the reset: the startup aborts without installing a link.
        await port.emit(
            connectedEvent({
                request_id: connect.request_id as string,
                connection_handle: PAIR_HANDLE,
            }),
        );
        await ackDisconnect(port, PAIR_HANDLE);
        await expect(pairing).resolves.toBe(false);
        await flush();

        expect(postsOf(port, 'send')).toHaveLength(0);
        expect(
            postsOf(port, 'disconnect').filter(
                (message) => message.connection_handle === PAIR_HANDLE,
            ),
        ).toHaveLength(1);
        expect(fixture.app.pairingState).toEqual({ phase: 'idle', finished: true });
        expect(await repository.peers()).toEqual([]);
        expect(fixture.local.snapshot?.peers).toEqual([]);
    });

    it('denies admission with the deleted identity when a connect completes during reset', async () => {
        const fixture = createAppFixture();
        await startApp(fixture);
        const repository = requireRepository(fixture);
        const phone = await TestPhone.create();
        const peerId = await addAppPeer(fixture, phone);
        // The identity the reset is about to delete; the connect path loads it into the cache.
        const oldIdentity = requireValue(fixture.keys.value).installationId;
        const port = fixture.controller.currentPort();

        const action = fixture.app.handleAction({
            type: 'connect',
            deviceHandle: HANDLE_DEVICE,
            peerId,
        });
        const connect = await port.nextPosted();
        expect(connect.type).toBe('connect');

        // Hold the reset inside its repository transaction, so the connect completion lands in
        // the exact window the old identity is still valid in the store but must not be admitted.
        const gate = deferred();
        const entered = fixture.local.nextWriteEntered();
        fixture.local.writeGate = gate.promise;
        fixture.local.holdMarkerWrite = 1;
        const resetting = fixture.app.resetTrust();
        await entered;
        await port.emit(
            connectedEvent({
                request_id: connect.request_id as string,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        await microFlush();

        // Admission is closed: no engine exists yet and no session was admitted with the
        // identity the reset deletes.
        expect(fixture.usedIdentities).toEqual([]);
        expect(fixture.app.sessions?.authenticatedSessions()).toEqual([]);

        gate.resolve();
        await resetting;
        const refreshed = await repository.identity();
        expect(refreshed.installationId).not.toBe(oldIdentity);

        // Admission is released only now, and the session opens with the refreshed identity.
        const hello = await nextSend(port);
        await ackSend(port, hello);
        const side = new PhoneSide(phone);
        await emitRecord(
            port,
            HANDLE_CONNECTION,
            await side.acceptClientHello(decodeFrame(hello.bytes).payload),
        );
        await flush();
        await action;

        expect(fixture.usedIdentities).toHaveLength(1);
        expect(hex(requireValue(fixture.usedIdentities[0]))).toBe(refreshed.installationId);
        expect(hex(requireValue(fixture.usedIdentities[0]))).not.toBe(oldIdentity);
        // The reset deleted the peer record, so that session cannot authenticate either.
        expect(fixture.app.sessions?.authenticatedSessions()).toEqual([]);
    });

    it('reports ok after a successful reset recovers a corrupt store', async () => {
        const fixture = createAppFixture();
        await startApp(fixture);
        const repository = requireRepository(fixture);
        await repository.identity();
        // A durable snapshot the store cannot decode fails closed, exactly like a corrupt store.
        const corrupt = structuredClone(requireValue(fixture.local.snapshot));
        (corrupt as unknown as { version: number }).version = 2;
        fixture.local.snapshot = corrupt;

        await expect(repository.peers()).rejects.toThrow('re-pair');
        await microFlush();
        expect(fixture.app.trustStatus).toBe('re-pair-required');

        // The documented recovery: reset, then pair again. A successful reset leaves nothing to
        // re-pair, so the status returns to `ok` instead of leaving the panel on "Re-pair required"
        // and offering a second destructive reset of the trust the user just rebuilt.
        await fixture.app.resetTrust();
        expect(fixture.app.trustStatus).toBe('ok');
    });

    it('refuses admission after a failed reset that left the old identity in the store', async () => {
        const fixture = createAppFixture();
        await startApp(fixture);
        const repository = requireRepository(fixture);
        const phone = await TestPhone.create();
        const peerId = await addAppPeer(fixture, phone);
        const oldIdentity = requireValue(fixture.keys.value).installationId;
        const port = fixture.controller.currentPort();

        // The reset's invalid-marker write is the next durable write: it fails, so nothing was
        // deleted and the store still holds the old identity and its records.
        fixture.local.writeFaultAt = fixture.local.writeCount + 1;
        await expect(fixture.app.resetTrust()).rejects.toThrow('re-pair');
        expect(fixture.app.trustStatus).toBe('re-pair-required');
        expect((await repository.peers()).map((peer) => peer.installationId)).toEqual([peerId]);
        expect(requireValue(fixture.keys.value).installationId).toBe(oldIdentity);

        const action = fixture.app.handleAction({
            type: 'connect',
            deviceHandle: HANDLE_DEVICE,
            peerId,
        });
        const connect = await port.nextPosted();
        await port.emit(
            connectedEvent({
                request_id: connect.request_id as string,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        await flush();

        // The connect reloaded the still-durable identity, but a failed reset keeps admission
        // closed: no engine is ever created with it and no session becomes live.
        expect(fixture.usedIdentities).toEqual([]);
        expect(fixture.app.sessions?.authenticatedSessions()).toEqual([]);
        expect(fixture.app.trustStatus).toBe('re-pair-required');

        // The refused handle was already connected and belongs to no session, so nothing but
        // this path can close it: exactly one disconnect is issued for it, and its disappearance
        // must not schedule an authentication-failure recovery.
        const refusals = postsOf(port, 'disconnect').filter(
            (message) => message.connection_handle === HANDLE_CONNECTION,
        );
        expect(refusals).toHaveLength(1);
        await ackDisconnect(port, HANDLE_CONNECTION);
        await action;
        await flush();
        // No recovery cycle is armed for the refused handle, and advancing the injected clock
        // cannot produce a recovery connect or search for it.
        expect(fixture.controller.clock.pendingTimers).toBe(0);
        expect(port.posted.map((message) => (message as { type?: string }).type)).toEqual([
            'hello',
            'connect',
            'disconnect',
        ]);
        fixture.controller.clock.advance(1_000);
        await flush();
        expect(postsOf(port, 'connect')).toHaveLength(1);
        expect(postsOf(port, 'start_scan')).toHaveLength(0);
    });

    it('publishes re-pair-required when a reset fails outside the store', async () => {
        const fixture = createAppFixture();
        await startApp(fixture);
        const phone = await TestPhone.create();
        const peerId = await addAppPeer(fixture, phone);
        const port = fixture.controller.currentPort();
        expect(fixture.app.trustStatus).toBe('ok');

        // The failure never reaches the repository, so no store-failure episode reports it: only
        // the reset body itself can tell the surface that admission is now latched closed.
        const coordinator = (fixture.app as unknown as { coordinator: RevocationCoordinator })
            .coordinator;
        coordinator.reset = async () => {
            throw new Error('reset body failed outside the store');
        };
        await expect(fixture.app.resetTrust()).rejects.toThrow(
            'reset body failed outside the store',
        );
        expect(fixture.app.trustStatus).toBe('re-pair-required');

        // Admission stayed closed: the completion delivered now builds no engine and opens no
        // session, even though the store still holds the identity the reset never discarded.
        const action = fixture.app.handleAction({
            type: 'connect',
            deviceHandle: HANDLE_DEVICE,
            peerId,
        });
        const connect = await port.nextPosted();
        await port.emit(
            connectedEvent({
                request_id: connect.request_id as string,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        await flush();

        expect(fixture.usedIdentities).toEqual([]);
        expect(fixture.app.sessions?.authenticatedSessions()).toEqual([]);
        expect(fixture.app.trustStatus).toBe('re-pair-required');
        const refusals = postsOf(port, 'disconnect').filter(
            (message) => message.connection_handle === HANDLE_CONNECTION,
        );
        expect(refusals).toHaveLength(1);
        await ackDisconnect(port, HANDLE_CONNECTION);
        await action;
        await flush();
    });

    it('keeps admission closed while a second overlapping reset is still pending', async () => {
        const fixture = createAppFixture();
        await startApp(fixture);
        const repository = requireRepository(fixture);
        const phone = await TestPhone.create();
        const peerId = await addAppPeer(fixture, phone);
        const port = fixture.controller.currentPort();

        // A connect whose transport completion is delivered between the two resets.
        const action = fixture.app.handleAction({
            type: 'connect',
            deviceHandle: HANDLE_DEVICE,
            peerId,
        });
        const connect = await port.nextPosted();
        expect(connect.type).toBe('connect');

        // Hold the second request's invalid-marker write: the first reset finishes while the
        // second one is still inside its own store transaction.
        const gate = deferred();
        const entered = fixture.local.nextWriteEntered();
        fixture.local.writeGate = gate.promise;
        fixture.local.holdMarkerWrite = 2;
        const first = fixture.app.resetTrust();
        const second = fixture.app.resetTrust();
        await first;
        await entered;

        // The first reset is done while the second still owns admission: a completion delivered
        // now stays out, so it can never be admitted with the identity the second will delete.
        await port.emit(
            connectedEvent({
                request_id: connect.request_id as string,
                connection_handle: HANDLE_CONNECTION,
            }),
        );
        await microFlush();
        expect(fixture.usedIdentities).toEqual([]);
        expect(fixture.app.sessions?.authenticatedSessions()).toEqual([]);

        gate.resolve();
        await second;
        const refreshed = await repository.identity();

        // Only the last reset releases admission, and the session opens with its identity.
        const hello = await nextSend(port);
        await ackSend(port, hello);
        const side = new PhoneSide(phone);
        await emitRecord(
            port,
            HANDLE_CONNECTION,
            await side.acceptClientHello(decodeFrame(hello.bytes).payload),
        );
        await flush();
        await action;

        expect(fixture.usedIdentities).toHaveLength(1);
        expect(hex(requireValue(fixture.usedIdentities[0]))).toBe(refreshed.installationId);
        expect(fixture.app.sessions?.authenticatedSessions()).toEqual([]);
    });

    it('waits for a reset in flight before installing a pairing link', async () => {
        const fixture = createAppFixture();
        await startApp(fixture);
        const repository = requireRepository(fixture);
        const port = fixture.controller.currentPort();
        const phone = await PhonePeer.create({ code: CODE });
        // A seed identity exists before the reset, so the reset deletes a real one.
        const oldIdentity = requireValue(await repository.identity()).installationId;

        // Hold the reset inside its durable invalid-marker write, after `abortPairingStartup`.
        const gate = deferred();
        const entered = fixture.local.nextWriteEntered();
        fixture.local.writeGate = gate.promise;
        fixture.local.holdMarkerWrite = 1;
        const resetting = fixture.app.resetTrust();
        await entered;

        // A pairing begun during the reset must not install a link or reach the transport: it
        // would otherwise be absent from `closeAll()`'s snapshot and persist enrollment state
        // after the reset completed.
        const pairing = fixture.app.startPairing(PAIR_DEVICE, CODE);
        await microFlush();
        expect(postsOf(port, 'connect')).toHaveLength(0);
        expect(postsOf(port, 'send')).toHaveLength(0);
        expect(fixture.app.pairingState.phase).toBe('idle');

        gate.resolve();
        await resetting;
        const refreshed = await repository.identity();
        expect(refreshed.installationId).not.toBe(oldIdentity);

        // It resumes only after the reset, under the refreshed identity, and owns its attempt.
        const connect = await port.nextPosted();
        expect(connect.type).toBe('connect');
        await port.emit(
            connectedEvent({
                request_id: connect.request_id as string,
                connection_handle: PAIR_HANDLE,
            }),
        );
        const request = await nextSend(port);
        await ackSend(port, request);
        await emitRecord(port, PAIR_HANDLE, phone.offerFor(request.bytes));

        const point = await nextSend(port);
        await ackSend(port, point);
        await emitRecord(
            port,
            PAIR_HANDLE,
            encodePairingFrame(PairingFrameType.CA, phone.acceptPoint(point.bytes)),
        );

        const confirm = await nextSend(port);
        await ackSend(port, confirm);
        await phone.acceptConfirmB(confirm.bytes);

        await emitRecord(port, PAIR_HANDLE, await phone.identityFrame());
        const identity = await nextSend(port);
        await ackSend(port, identity);
        await phone.acceptClientIdentity(identity.bytes);

        await emitRecord(port, PAIR_HANDLE, await phone.prepareFrame());
        const commit = await nextSend(port);
        await ackSend(port, commit);

        await emitRecord(port, PAIR_HANDLE, await phone.completeFrame());
        const ack = await nextSend(port);
        await ackSend(port, ack);
        await expect(pairing).resolves.toBe(true);
        await ackDisconnect(port, PAIR_HANDLE);
        await flush();

        // The enrollment persisted one record under the fresh identity, never the deleted one.
        const peers = await repository.peers();
        expect(peers.map((peer) => peer.installationId)).toEqual([hexOf(phone.installationId)]);
        expect(peers[0]?.state).toBe('active_activation_pending');
        expect(requireValue(fixture.keys.value).installationId).toBe(refreshed.installationId);
        expect(requireValue(fixture.keys.value).installationId).not.toBe(oldIdentity);
    });

    it('refuses a pairing begun after a reset that could not discard the identity', async () => {
        const fixture = createAppFixture();
        await startApp(fixture);
        const repository = requireRepository(fixture);
        await repository.identity();
        const port = fixture.controller.currentPort();

        // The reset's invalid-marker write fails, so admission stays latched closed.
        fixture.local.writeFaultAt = fixture.local.writeCount + 1;
        await expect(fixture.app.resetTrust()).rejects.toThrow('re-pair');

        await expect(fixture.app.startPairing(PAIR_DEVICE, CODE)).resolves.toBe(false);
        expect(postsOf(port, 'connect')).toHaveLength(0);
        expect(fixture.app.pairingState).toEqual({ phase: 'idle', finished: true });
    });

    it('resets, re-pairs and reconnects with a refreshed identity cache', async () => {
        const fixture = createAppFixture();
        await startApp(fixture);
        const repository = requireRepository(fixture);
        const installationId = crypto.getRandomValues(new Uint8Array(16));
        const identity = (await crypto.subtle.generateKey(
            { name: 'ECDSA', namedCurve: 'P-256' },
            true,
            ['sign', 'verify'],
        )) as CryptoKeyPair;
        const phone = await TestPhone.create({ identity, installationId });
        const phonePeer = await PhonePeer.create({ code: CODE, identity, installationId });
        const peerId = await addAppPeer(fixture, phone);
        await connectThroughApp(fixture, phone, peerId);
        const originalIdentity = requireValue(fixture.usedIdentities[0]);
        const originalHex = hex(originalIdentity);
        expect(fixture.app.sessions?.authenticatedSessions()).toHaveLength(1);

        await fixture.app.resetTrust();

        // Reset terminates first, then discards the trust and the identity.
        await ackDisconnect(fixture.controller.currentPort(), HANDLE_CONNECTION);
        expect(fixture.app.sessions?.authenticatedSessions()).toEqual([]);
        expect(await repository.peers()).toEqual([]);
        const refreshed = await repository.identity();
        expect(refreshed.installationId).not.toBe(originalHex);

        // Re-pair the phone through the connector's own pairing flow.
        await pairThroughApp(fixture, phonePeer, PAIR_DEVICE_2);
        const repaired = (await repository.peers()).find(
            (peer) => peer.installationId === hexOf(phonePeer.installationId),
        );
        // The pairing created a fresh record under the refreshed identity.
        expect(repaired?.state).toBe('active_activation_pending');

        // The next engine is created with the refreshed identity, never the deleted one.
        await connectThroughApp(
            fixture,
            phone,
            hexOf(phonePeer.installationId),
            HANDLE_CONNECTION_2,
            HANDLE_DEVICE_2,
        );
        const used = requireValue(fixture.usedIdentities.at(-1));
        expect(hex(used)).toBe(refreshed.installationId);
        expect(hex(used)).not.toBe(originalHex);
        expect(fixture.app.sessions?.authenticatedSessions()).toHaveLength(1);
    });
});
