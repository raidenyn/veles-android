import { readFileSync } from 'node:fs';
import { afterEach, beforeAll, describe, expect, it } from 'vitest';

import {
    ConnectorApp,
    type ConnectorControllerLike,
    type ConnectorView,
} from '../../src/connector';
import { ConnectorState } from '../../src/connector/state';
import type { ConnectorResult } from '../../src/connector/port';
import type { WireEvent } from '../../src/connector/protocol';
import { synthesizePortClosed } from '../../src/connector/errors';
import { ClientMachine, type ClientTrustRepository } from '../../src/pairing/client-machine';
import type { PairingCore } from '../../src/pairing/core';
import { PairingFrameType, encodePairingFrame } from '../../src/pairing/frames';
import {
    fingerprintDisplay,
    isEnrollmentKeysWiped,
    pairFingerprint,
} from '../../src/pairing/enrollment';
import { initPairingCore, openHandleCount, PairingExecution } from '../../src/pairing/core';
import { PairingLink, type PairingTransport } from '../../src/pairing/link';
import { RecordType } from '../../src/session/wire';
import { bytes } from '../../src/trust/model';
import type { ChromeTrustRepository } from '../../src/trust/repository';
import {
    SessionClock,
    createTestTrust,
    flush,
    sessionEngineDeps,
} from '../connector/session-harness';
import { PACKAGE_BYTES, PhonePeer, hexOf, openPhoneHandleCount } from './enrollment-harness';

beforeAll(async () => {
    await initPairingCore(readFileSync(PACKAGE_BYTES));
});

interface Run {
    clock: SessionClock;
    trust: Awaited<ReturnType<typeof createTestTrust>>;
    phone: PhonePeer;
    machine: ClientMachine;
    repo: ClientTrustRepository;
}

async function setup(
    options: { phoneCode?: string; code?: string; repo?: ClientTrustRepository } = {},
): Promise<Run> {
    const clock = new SessionClock();
    const trust = await createTestTrust(clock);
    const code = options.code ?? '000042';
    const phone = await PhonePeer.create({ code: options.phoneCode ?? code });
    const repo = options.repo ?? trust.repository;
    const machine = new ClientMachine({
        core: PairingExecution,
        repo,
        clock: clock.now,
        code,
    });
    return { clock, trust, phone, machine, repo };
}

function caFrame(cA: Uint8Array): Uint8Array {
    return encodePairingFrame(PairingFrameType.CA, cA);
}

/** Drives the client through the offer and its own pB, leaving it in `awaiting_ca`. */
async function toAwaitingCa(run: Run): Promise<Uint8Array> {
    const request = run.machine.start();
    const offer = run.phone.offerFor(request);
    const out = await run.machine.onFrame(offer);
    expect(out).toHaveLength(1);
    expect(out[0][0]).toBe(PairingFrameType.PB);
    return run.phone.acceptPoint(out[0]);
}

/** Drives through a verified cA to `awaiting_phone_identity`. */
async function toAwaitingPhoneIdentity(run: Run): Promise<void> {
    const cA = await toAwaitingCa(run);
    const out = await run.machine.onFrame(caFrame(cA));
    expect(out).toHaveLength(1);
    expect(out[0][0]).toBe(PairingFrameType.CB);
    await run.phone.acceptConfirmB(out[0]);
}

/** Drives through the phone identity and the client's own release to `awaiting_prepare`. */
async function toAwaitingPrepare(run: Run): Promise<void> {
    await toAwaitingPhoneIdentity(run);
    const identity = await run.phone.identityFrame();
    const out = await run.machine.onFrame(identity);
    expect(out).toHaveLength(1);
    await run.phone.acceptClientIdentity(out[0]);
    expect(run.machine.state).toBe('awaiting_prepare');
}

/** Drives through the verified prepare to `verified_prepare_ready`. */
async function toVerifiedPrepare(run: Run): Promise<void> {
    await toAwaitingPrepare(run);
    const out = await run.machine.onFrame(await run.phone.prepareFrame());
    expect(out).toEqual([]);
    expect(run.machine.state).toBe('verified_prepare_ready');
}

/** Drives through commit to `awaiting_complete`. */
async function toAwaitingComplete(run: Run): Promise<Uint8Array> {
    await toVerifiedPrepare(run);
    const commit = await run.machine.commit();
    expect(run.machine.state).toBe('awaiting_complete');
    return commit;
}

/** The record type from the plaintext §7 header (type is the u16 at offset 3). */
function recordTypeOf(record: Uint8Array): number {
    return ((record[3] & 0xff) << 8) | (record[4] & 0xff);
}

function recordingCore(): { core: PairingCore; calls: string[] } {
    const calls: string[] = [];
    return {
        calls,
        core: {
            start(code: string, context: Uint8Array) {
                calls.push('start');
                const started = PairingExecution.start(code, context);
                return {
                    point: started.point,
                    execution: {
                        exchange(point: Uint8Array) {
                            calls.push('exchange');
                            return started.execution.exchange(point);
                        },
                        confirm(confirmation: Uint8Array) {
                            calls.push('confirm');
                            return started.execution.confirm(confirmation);
                        },
                        finish() {
                            calls.push('finish');
                            return started.execution.finish();
                        },
                        close() {
                            calls.push('close');
                            started.execution.close();
                        },
                    },
                };
            },
        },
    };
}

class DelayedRepository implements ClientTrustRepository {
    readonly gate: Promise<void>;
    private release!: () => void;
    addPendingStarted = false;
    addPendingFinished = false;

    constructor(private readonly delegate: ChromeTrustRepository) {
        this.gate = new Promise<void>((resolve) => {
            this.release = resolve;
        });
    }

    identity() {
        return this.delegate.identity();
    }
    peers() {
        return this.delegate.peers();
    }
    async addPending(peer: Parameters<ChromeTrustRepository['addPending']>[0]): Promise<void> {
        this.addPendingStarted = true;
        await this.gate;
        await this.delegate.addPending(peer);
        this.addPendingFinished = true;
    }
    confirm(
        peerId: string,
        stage: Parameters<ChromeTrustRepository['confirm']>[1],
        proof: Uint8Array,
    ) {
        return this.delegate.confirm(peerId, stage, proof);
    }
    revoke(peerId: string) {
        return this.delegate.revoke(peerId);
    }
    open(): void {
        this.release();
    }
}

class FakeTransport implements PairingTransport {
    readonly sent: Uint8Array[] = [];
    readonly handles: string[] = [];
    disconnected = false;
    private listeners = new Set<
        (record: { connectionHandle: string; bytes: Uint8Array }) => void
    >();

    async connect(target: { deviceHandle: string }) {
        this.handles.push(target.deviceHandle);
        return {
            ok: true as const,
            event: {
                type: 'connected' as const,
                envelope_version: 1 as const,
                api_version: 1,
                request_id: `connect-${this.handles.length}`,
                connection_handle: 'c1',
            },
        };
    }

    async send(_handle: string, record: Uint8Array) {
        this.sent.push(record.slice());
        return {
            ok: true as const,
            event: {
                type: 'sent' as const,
                envelope_version: 1 as const,
                api_version: 1,
                request_id: `send-${this.sent.length}`,
                connection_handle: 'c1',
            },
        };
    }

    async disconnect() {
        this.disconnected = true;
        return {
            ok: true as const,
            event: {
                type: 'disconnected' as const,
                envelope_version: 1 as const,
                api_version: 1,
                connection_handle: 'c1',
            },
        };
    }

    onRecord(
        listener: (record: { connectionHandle: string; bytes: Uint8Array }) => void,
    ): () => void {
        this.listeners.add(listener);
        return () => {
            this.listeners.delete(listener);
        };
    }

    emit(bytes: Uint8Array): void {
        for (const listener of this.listeners) listener({ connectionHandle: 'c1', bytes });
    }
}

type ConnectedEvent = Extract<WireEvent, { type: 'connected' }>;
type SentEvent = Extract<WireEvent, { type: 'sent' }>;
type DisconnectedEvent = Extract<WireEvent, { type: 'disconnected' }>;
type ScanStateEvent = Extract<WireEvent, { type: 'scan_state' }>;
type ReadyEvent = Extract<WireEvent, { type: 'ready' }>;
type ShutdownCompleteEvent = Extract<WireEvent, { type: 'shutdown_complete' }>;

interface PendingConnect {
    readonly handle: string;
    resolve(result: ConnectorResult<ConnectedEvent>): void;
}

interface PendingSend {
    readonly handle: string;
    resolve(result: ConnectorResult<SentEvent>): void;
}

/**
 * A controller whose transport `connect` stays pending until the test settles it,
 * so a pairing startup can be observed mid-flight. `close` is deliberately inert:
 * it lets `dispose()` race a connect that later resolves successfully. A send can
 * be deferred once, and inbound records can be delivered to the installed
 * listener, to observe a startup between its connect and its first frame.
 */
class DeferredPairingController implements ConnectorControllerLike {
    readonly state = new ConnectorState();
    readonly connects: PendingConnect[] = [];
    readonly sent: Uint8Array[] = [];
    readonly disconnected: string[] = [];
    readonly pendingSends: PendingSend[] = [];
    readonly recordListeners = new Set<
        (record: { connectionHandle: string; bytes: Uint8Array }) => void
    >();
    portGeneration = 0;
    private nextHandle = 1;
    private deferNextSend = false;

    connect(): Promise<ConnectorResult<ConnectedEvent>> {
        const handle = `link-${this.nextHandle}`;
        this.nextHandle += 1;
        return new Promise<ConnectorResult<ConnectedEvent>>((resolve) => {
            this.connects.push({ handle, resolve });
        });
    }

    /** Settles a pending connect with a successful transport link. */
    acceptConnect(index: number): void {
        const pending = this.connects[index];
        if (pending === undefined) throw new Error(`connect ${index} was not issued`);
        pending.resolve({
            ok: true,
            event: {
                type: 'connected',
                envelope_version: 1,
                api_version: 1,
                request_id: `connect-${index}`,
                connection_handle: pending.handle,
            },
        });
    }

    /** Holds the next send until `acceptSend`, exposing the startup window. */
    deferSendOnce(): void {
        this.deferNextSend = true;
    }

    /** Delivers an inbound record to every installed listener. */
    emit(handle: string, bytes: Uint8Array): void {
        for (const listener of this.recordListeners) listener({ connectionHandle: handle, bytes });
    }

    async send(handle: string, record: Uint8Array): Promise<ConnectorResult<SentEvent>> {
        this.sent.push(record.slice());
        if (this.deferNextSend) {
            this.deferNextSend = false;
            return new Promise<ConnectorResult<SentEvent>>((resolve) => {
                this.pendingSends.push({ handle, resolve });
            });
        }
        return {
            ok: true,
            event: {
                type: 'sent',
                envelope_version: 1,
                api_version: 1,
                request_id: `send-${this.sent.length}`,
                connection_handle: handle,
            },
        };
    }

    /** Settles a deferred send successfully. */
    acceptSend(index: number): void {
        const pending = this.pendingSends[index];
        if (pending === undefined) throw new Error(`send ${index} was not issued`);
        pending.resolve({
            ok: true,
            event: {
                type: 'sent',
                envelope_version: 1,
                api_version: 1,
                request_id: `sent-${index}`,
                connection_handle: pending.handle,
            },
        });
    }

    async disconnect(handle: string): Promise<ConnectorResult<DisconnectedEvent>> {
        this.disconnected.push(handle);
        return {
            ok: true,
            event: {
                type: 'disconnected',
                envelope_version: 1,
                api_version: 1,
                connection_handle: handle,
            },
        };
    }

    onRecord(
        listener: (record: { connectionHandle: string; bytes: Uint8Array }) => void,
    ): () => void {
        this.recordListeners.add(listener);
        return () => {
            this.recordListeners.delete(listener);
        };
    }
    onChange(): () => void {
        return () => undefined;
    }
    onClose(): () => void {
        return () => undefined;
    }
    onFailure(): () => void {
        return () => undefined;
    }
    onWireError(): () => void {
        return () => undefined;
    }
    isOpen(): boolean {
        return true;
    }
    snapshot() {
        return this.state.snapshot();
    }
    open(): void {
        // The deferred controller is always open; pairing never reopens the port.
    }
    close(): Promise<void> {
        return Promise.resolve();
    }
    hello(): Promise<ConnectorResult<ReadyEvent>> {
        return Promise.resolve({
            ok: true,
            event: {
                type: 'ready',
                envelope_version: 1,
                api_version: 1,
                request_id: 'hello',
                bridge_version: '1.0.0',
                platform: 'windows',
                capabilities: [],
            },
        });
    }
    startScan(): Promise<ConnectorResult<ScanStateEvent>> {
        return Promise.resolve({
            ok: false,
            source: 'local',
            error: synthesizePortClosed('start_scan'),
        });
    }
    stopScan(): Promise<ConnectorResult<ScanStateEvent>> {
        return Promise.resolve({
            ok: false,
            source: 'local',
            error: synthesizePortClosed('stop_scan'),
        });
    }
    shutdown(): Promise<ConnectorResult<ShutdownCompleteEvent>> {
        return Promise.resolve({
            ok: false,
            source: 'local',
            error: synthesizePortClosed('shutdown'),
        });
    }
}

class SilentView implements ConnectorView {
    render(): void {
        // The pairing race tests assert on the machine and transport, not the view.
    }
    onAction(): () => void {
        return () => undefined;
    }
}

/** A real trust repository and one connector app over the deferred controller. */
async function createPairingApp(): Promise<{
    app: ConnectorApp;
    controller: DeferredPairingController;
    trust: Awaited<ReturnType<typeof createTestTrust>>;
}> {
    const clock = new SessionClock();
    const trust = await createTestTrust(clock);
    const controller = new DeferredPairingController();
    const app = new ConnectorApp({
        controller,
        view: new SilentView(),
        session: {
            createRepository(verifier) {
                trust.bindVerifier(verifier);
                return trust.repository;
            },
            createEngineDeps(handle, peer, localInstallationId) {
                return sessionEngineDeps(trust, clock, localInstallationId)(handle, peer);
            },
        },
    });
    return { app, controller, trust };
}

describe('client enrollment machine', () => {
    afterEach(() => {
        expect(openHandleCount()).toBe(0);
        expect(openPhoneHandleCount()).toBe(0);
    });

    it('wrong_code_cA_fails_no_cB_sent', async () => {
        const run = await setup({ phoneCode: '111111' });
        try {
            const cA = await toAwaitingCa(run);
            const out = await run.machine.onFrame(caFrame(cA));
            expect(out.some((frame) => frame[0] === PairingFrameType.CB)).toBe(false);
            expect(run.machine.state).toBe('idle');
            expect(run.machine.failed).toBe(true);
            expect(run.machine.enrollmentKeysForTest()).toBeNull();
            expect(openHandleCount()).toBe(0);
            expect(await run.repo.peers()).toEqual([]);
        } finally {
            run.phone.close();
        }
    });

    it('keys_derived_only_after_cA_verified', async () => {
        const first = await setup({ phoneCode: '111111' });
        const recording = recordingCore();
        first.machine = new ClientMachine({
            core: recording.core,
            repo: first.repo,
            clock: first.clock.now,
            code: '000042',
        });
        const second = await setup();
        try {
            const wrongCa = await toAwaitingCa(first);
            expect(first.machine.enrollmentKeysForTest()).toBeNull();
            expect(recording.calls).not.toContain('finish');
            await first.machine.onFrame(caFrame(wrongCa));
            expect(first.machine.enrollmentKeysForTest()).toBeNull();
            expect(recording.calls).not.toContain('finish');

            const goodCa = await toAwaitingCa(second);
            const out = await second.machine.onFrame(caFrame(goodCa));
            expect(out[0][0]).toBe(PairingFrameType.CB);
            const keys = second.machine.enrollmentKeysForTest();
            expect(keys).not.toBeNull();
            if (keys !== null) expect(isEnrollmentKeysWiped(keys)).toBe(false);
        } finally {
            first.phone.close();
            second.phone.close();
        }
    });

    it('client_releases_record_only_after_phone_record_authenticated', async () => {
        const run = await setup();
        try {
            await toAwaitingPhoneIdentity(run);
            expect(run.machine.state).toBe('awaiting_phone_identity');
            const forged = await run.phone.identityFrame({ fingerprintOk: false });
            const out = await run.machine.onFrame(forged);
            expect(out).toEqual([]);
            expect(run.machine.state).toBe('idle');
            expect(run.machine.failed).toBe(true);
            expect(run.machine.fingerprint()).toBeUndefined();
            expect(await run.repo.peers()).toEqual([]);
            expect(run.machine.enrollmentKeysForTest()).toBeNull();

            const valid = await setup();
            try {
                await toAwaitingPhoneIdentity(valid);
                const identity = await valid.phone.identityFrame();
                const frames = await valid.machine.onFrame(identity);
                expect(frames).toHaveLength(1);
                expect(frames[0][2]).toBe(1);
                expect(((frames[0][3] & 0xff) << 8) | (frames[0][4] & 0xff)).toBe(0x0100);
                expect(valid.machine.state).toBe('awaiting_prepare');
                const peers = await valid.repo.peers();
                expect(peers).toHaveLength(1);
                expect(peers[0].installationId).toBe(hexOf(valid.phone.installationId));
            } finally {
                valid.phone.close();
            }
        } finally {
            run.phone.close();
        }
    });

    it('client_persists_pending_before_release', async () => {
        const clock = new SessionClock();
        const trust = await createTestTrust(clock);
        const delayed = new DelayedRepository(trust.repository);
        const run = await setup({ repo: delayed });
        run.machine = new ClientMachine({
            core: PairingExecution,
            repo: delayed,
            clock: clock.now,
            code: '000042',
        });
        try {
            await toAwaitingPhoneIdentity(run);
            const identity = await run.phone.identityFrame();
            let settled = false;
            const pending = run.machine.onFrame(identity).then((frames) => {
                settled = true;
                return frames;
            });
            await flush();
            expect(delayed.addPendingStarted).toBe(true);
            expect(delayed.addPendingFinished).toBe(false);
            expect(settled).toBe(false);
            expect(await trust.repository.peers()).toEqual([]);

            delayed.open();
            const frames = await pending;
            expect(frames).toHaveLength(1);
            expect(delayed.addPendingFinished).toBe(true);
            const peers = await trust.repository.peers();
            expect(peers).toHaveLength(1);
            expect(peers[0].state).toBe('pending');
            await run.phone.acceptClientIdentity(frames[0]);
            expect(peers[0].enrollmentTranscript).toBe(hexOf(await run.phone.expectedTranscript()));
        } finally {
            run.phone.close();
        }
    });

    it('rejected_after_pending_deletes_record', async () => {
        const run = await setup();
        try {
            await toAwaitingPrepare(run);
            expect(await run.repo.peers()).toHaveLength(1);
            const out = await run.machine.onFrame(await run.phone.appErrorFrame(0x04));
            expect(out).toEqual([]);
            expect(run.machine.state).toBe('idle');
            expect(run.machine.failed).toBe(true);
            expect(run.machine.fingerprint()).toBeUndefined();
            expect(run.machine.enrollmentKeysForTest()).toBeNull();
            expect(await run.repo.peers()).toEqual([]);
            expect(openHandleCount()).toBe(0);
        } finally {
            run.phone.close();
        }
    });

    it('client_timeouts', async () => {
        const offerOnly = await setup();
        try {
            offerOnly.machine.start();
            offerOnly.clock.advance(29_999);
            expect(await offerOnly.machine.onTick()).toBe(false);
            expect(offerOnly.machine.state).toBe('awaiting_offer');
            offerOnly.clock.advance(1);
            expect(await offerOnly.machine.onTick()).toBe(true);
            expect(offerOnly.machine.state).toBe('idle');
            expect(offerOnly.machine.failed).toBe(true);
            expect(openHandleCount()).toBe(0);
            expect(await offerOnly.repo.peers()).toEqual([]);
        } finally {
            offerOnly.phone.close();
        }

        const preparing = await setup();
        try {
            await toAwaitingPrepare(preparing);
            preparing.clock.advance(30_000);
            expect(await preparing.machine.onTick()).toBe(true);
            expect(preparing.machine.state).toBe('idle');
            expect(preparing.machine.failed).toBe(true);
            expect(preparing.machine.enrollmentKeysForTest()).toBeNull();
            expect(openHandleCount()).toBe(0);
            const peers = await preparing.repo.peers();
            expect(peers).toHaveLength(1);
            expect(peers[0].state).toBe('pending');
            await expect(preparing.machine.commit()).rejects.toThrow();
        } finally {
            preparing.phone.close();
        }

        const completing = await setup();
        try {
            await toAwaitingComplete(completing);
            completing.clock.advance(30_000);
            expect(await completing.machine.onTick()).toBe(true);
            expect(completing.machine.state).toBe('idle');
            expect(completing.machine.failed).toBe(true);
            expect(completing.machine.enrollmentKeysForTest()).toBeNull();
            expect(await completing.repo.peers()).toHaveLength(1);
        } finally {
            completing.phone.close();
        }
    });

    it('cancel_deletes_pending_record', async () => {
        const run = await setup();
        const other = await PhonePeer.create({ code: '000042' });
        try {
            await run.repo.addPending({
                installationId: hexOf(other.installationId),
                publicKey: hexOf(other.identityPubkey),
                label: 'Other phone',
                deviceType: 0,
                enrollmentTranscript: 'ab'.repeat(32),
            });
            const seeded = await run.repo.peers();
            await toAwaitingPrepare(run);
            expect(await run.repo.peers()).toHaveLength(2);

            await run.machine.cancel();

            expect(run.machine.state).toBe('idle');
            expect(run.machine.closed).toBe(true);
            expect(run.machine.fingerprint()).toBeUndefined();
            expect(run.machine.enrollmentKeysForTest()).toBeNull();
            expect(openHandleCount()).toBe(0);
            const remaining = await run.repo.peers();
            expect(remaining).toEqual(seeded);
        } finally {
            run.phone.close();
            other.close();
        }
    });

    it('cancel_keeps_recovered_active_record', async () => {
        const run = await setup();
        try {
            await toAwaitingPrepare(run);
            const [pending] = await run.repo.peers();
            // A recovery session on the same connector store authenticates the pending
            // record while this abandoned link still holds its id.
            run.trust.bindVerifier(async () => true);
            await run.trust.repository.confirm(
                pending.installationId,
                'complete',
                new Uint8Array(32),
            );
            expect((await run.repo.peers())[0].state).toBe('active_activation_pending');

            expect(await run.machine.cancel()).toBe(true);

            expect(run.machine.state).toBe('idle');
            const remaining = await run.repo.peers();
            expect(remaining.map((peer) => peer.installationId)).toEqual([pending.installationId]);
            expect(remaining[0].state).toBe('active_activation_pending');
        } finally {
            run.phone.close();
        }
    });

    it('cancel_cleanup_failure_is_visible', async () => {
        const clock = new SessionClock();
        const base = await createTestTrust(clock);
        const failing: ClientTrustRepository = {
            identity: () => base.repository.identity(),
            peers: () => base.repository.peers(),
            addPending: (peer) => base.repository.addPending(peer),
            confirm: (peerId, stage, proof) => base.repository.confirm(peerId, stage, proof),
            revoke: async () => {
                throw new Error('trust store unavailable');
            },
        };
        const run = await setup({ repo: failing });
        try {
            await toAwaitingPrepare(run);
            expect(await run.repo.peers()).toHaveLength(1);

            expect(await run.machine.cancel()).toBe(false);

            expect(run.machine.state).toBe('idle');
            expect(run.machine.closed).toBe(true);
            expect(openHandleCount()).toBe(0);
        } finally {
            run.phone.close();
        }
    });

    it('tab_teardown_closes_handle_and_deletes_pending', async () => {
        const clock = new SessionClock();
        const trust = await createTestTrust(clock);
        const transport = new FakeTransport();
        const phone = await PhonePeer.create({ code: '000042' });
        const link = await PairingLink.start({
            controller: transport,
            repo: trust.repository,
            code: '000042',
            clock: clock.now,
            deviceHandle: 'device-1',
            tickIntervalMs: 0,
        });
        try {
            expect(transport.sent).toHaveLength(1);
            const offer = phone.offerFor(transport.sent[0]);
            await link.receive(offer);
            expect(transport.sent).toHaveLength(2);
            expect(openHandleCount()).toBe(1);
            await link.dispose();
            expect(openHandleCount()).toBe(0);
            expect(transport.disconnected).toBe(true);
            expect(await trust.repository.peers()).toEqual([]);
            expect(link.machine.state).toBe('idle');
        } finally {
            phone.close();
        }

        const second = await createTestTrust(clock);
        const secondTransport = new FakeTransport();
        const secondPhone = await PhonePeer.create({ code: '000042' });
        const secondLink = await PairingLink.start({
            controller: secondTransport,
            repo: second.repository,
            code: '000042',
            clock: clock.now,
            deviceHandle: 'device-2',
            tickIntervalMs: 0,
        });
        try {
            const offer = secondPhone.offerFor(secondTransport.sent[0]);
            await secondLink.receive(offer);
            const cA = secondPhone.acceptPoint(secondTransport.sent[1]);
            await secondLink.receive(caFrame(cA));
            await secondPhone.acceptConfirmB(secondTransport.sent[2]);
            const identity = await secondPhone.identityFrame();
            await secondLink.receive(identity);
            await secondPhone.acceptClientIdentity(secondTransport.sent[3]);
            expect(await second.repository.peers()).toHaveLength(1);

            await secondLink.dispose();

            expect(await second.repository.peers()).toEqual([]);
            expect(openHandleCount()).toBe(0);
            expect(secondLink.machine.fingerprint()).toBeUndefined();
        } finally {
            secondPhone.close();
        }
    });

    it('link_abandon_keeps_pending_and_reports_end_once', async () => {
        const clock = new SessionClock();
        const trust = await createTestTrust(clock);
        const transport = new FakeTransport();
        const phone = await PhonePeer.create({ code: '000042' });
        let ended = 0;
        let framesSentAtConnect: number | null = null;
        const link = await PairingLink.start({
            controller: transport,
            repo: trust.repository,
            code: '000042',
            clock: clock.now,
            deviceHandle: 'device-3',
            tickIntervalMs: 0,
            onConnected: () => {
                framesSentAtConnect = transport.sent.length;
            },
            onEnded: () => {
                ended += 1;
            },
        });
        try {
            expect(framesSentAtConnect).toBe(0);
            const offer = phone.offerFor(transport.sent[0]);
            await link.receive(offer);
            const cA = phone.acceptPoint(transport.sent[1]);
            await link.receive(caFrame(cA));
            await phone.acceptConfirmB(transport.sent[2]);
            const identity = await phone.identityFrame();
            await link.receive(identity);
            await phone.acceptClientIdentity(transport.sent[3]);
            expect(await trust.repository.peers()).toHaveLength(1);

            // A connection loss while the connector document lives closes the
            // handle but preserves the durable pending record (OTP2-STA-010d).
            await link.abandon();

            expect(await trust.repository.peers()).toHaveLength(1);
            expect(openHandleCount()).toBe(0);
            expect(link.machine.state).toBe('idle');
            expect(ended).toBe(1);
            // The real tab teardown still deletes the record the dead link kept.
            await link.dispose();
            expect(await trust.repository.peers()).toEqual([]);
            expect(ended).toBe(1);
        } finally {
            phone.close();
        }
    });

    it('link_sends_the_activation_commit_after_a_verified_prepare', async () => {
        const clock = new SessionClock();
        const trust = await createTestTrust(clock);
        const transport = new FakeTransport();
        const phone = await PhonePeer.create({ code: '000042' });
        const link = await PairingLink.start({
            controller: transport,
            repo: trust.repository,
            code: '000042',
            clock: clock.now,
            deviceHandle: 'device-4',
            tickIntervalMs: 0,
        });
        try {
            const offer = phone.offerFor(transport.sent[0]);
            await link.receive(offer);
            const cA = phone.acceptPoint(transport.sent[1]);
            await link.receive(caFrame(cA));
            await phone.acceptConfirmB(transport.sent[2]);
            const identity = await phone.identityFrame();
            await link.receive(identity);
            await phone.acceptClientIdentity(transport.sent[3]);
            expect(transport.sent).toHaveLength(4);

            // No manual `machine.commit()`: a verified prepare must make the link send
            // the one activation commit itself.
            await link.receive(await phone.prepareFrame());

            const commits = transport.sent.filter(
                (record) => recordTypeOf(record) === RecordType.ACTIVATION_COMMIT,
            );
            expect(commits).toHaveLength(1);
            expect(transport.sent).toHaveLength(5);
            expect(transport.sent[4]).toBe(commits[0]);
            expect(link.machine.state).toBe('awaiting_complete');
        } finally {
            await link.dispose();
            phone.close();
        }
    });

    it('fingerprint_only_after_both_authenticated', async () => {
        const run = await setup();
        try {
            const cA = await toAwaitingCa(run);
            expect(run.machine.fingerprint()).toBeUndefined();
            const out = await run.machine.onFrame(caFrame(cA));
            await run.phone.acceptConfirmB(out[0]);
            expect(run.machine.state).toBe('awaiting_phone_identity');
            expect(run.machine.fingerprint()).toBeUndefined();

            const identity = await run.phone.identityFrame();
            const frames = await run.machine.onFrame(identity);
            expect(frames).toHaveLength(1);
            await run.phone.acceptClientIdentity(frames[0]);
            const expected = fingerprintDisplay(
                await pairFingerprint(
                    run.phone.identityPubkey,
                    bytes(run.trust.identity.publicKey, 65),
                ),
            );
            expect(run.machine.fingerprint()).toBe(expected);

            await run.machine.cancel();
            expect(run.machine.fingerprint()).toBeUndefined();
        } finally {
            run.phone.close();
        }
    });

    it('unauthorized_record_before_active_clears_fingerprint', async () => {
        const run = await setup();
        try {
            await toAwaitingPrepare(run);
            expect(run.machine.fingerprint()).toBeDefined();
            const out = await run.machine.onFrame(await run.phone.strayPushFrame());
            expect(out).toEqual([]);
            expect(run.machine.state).toBe('idle');
            expect(run.machine.failed).toBe(true);
            expect(run.machine.closed).toBe(true);
            expect(run.machine.fingerprint()).toBeUndefined();
            // Only the active-family mirror survives a post-activation refusal;
            // this pre-active refusal keeps the pending record for recovery.
            expect(await run.repo.peers()).toHaveLength(1);
        } finally {
            run.phone.close();
        }
    });

    it('duplicate_id_takes_persist_failure_path', async () => {
        const clock = new SessionClock();
        const trust = await createTestTrust(clock);
        const phone = await PhonePeer.create({ code: '000042' });
        await trust.repository.addPending({
            installationId: hexOf(phone.installationId),
            publicKey: hexOf(phone.identityPubkey),
            label: 'Existing phone',
            deviceType: 0,
            enrollmentTranscript: 'cd'.repeat(32),
        });
        const before = await trust.repository.peers();
        const machine = new ClientMachine({
            core: PairingExecution,
            repo: trust.repository,
            clock: clock.now,
            code: '000042',
        });
        const run: Run = { clock, trust, phone, machine, repo: trust.repository };
        try {
            await toAwaitingPhoneIdentity(run);
            const identity = await run.phone.identityFrame();
            const out = await run.machine.onFrame(identity);
            expect(out).toEqual([]);
            expect(run.machine.state).toBe('idle');
            expect(run.machine.failed).toBe(true);
            expect(run.machine.fingerprint()).toBeUndefined();
            expect(run.machine.enrollmentKeysForTest()).toBeNull();
            expect(openHandleCount()).toBe(0);
            expect(await run.repo.peers()).toEqual(before);
        } finally {
            run.phone.close();
        }
    });

    it('cancel_during_pairing_startup_aborts_before_the_first_frame', async () => {
        const { app, controller, trust } = await createPairingApp();
        try {
            const pairing = app.startPairing('device-1', '000042');
            await flush();
            expect(controller.connects).toHaveLength(1);

            // The user cancels while the transport connect is still pending: the
            // startup has no `pairingLink` to cancel yet, so the generation makes it
            // stale. `start` observes that right after connect and closes the
            // transport without ever sending its pairing_request.
            expect(await app.cancelPairing()).toBe(true);
            controller.acceptConnect(0);

            expect(await pairing).toBe(false);
            expect(controller.sent).toEqual([]);
            expect(controller.disconnected).toEqual(['link-1']);
            expect(app.pairingState).toEqual({ phase: 'idle', finished: true });
            expect(openHandleCount()).toBe(0);
            expect(await trust.repository.peers()).toEqual([]);
        } finally {
            app.dispose();
        }
    });

    it('cancel_during_pairing_startup_never_processes_an_offer', async () => {
        const { app, controller, trust } = await createPairingApp();
        const phone = await PhonePeer.create({ code: '000042' });
        try {
            // Hold the initial pairing_request: the link is connected, its record
            // listener is installed, and the startup has not returned yet.
            controller.deferSendOnce();
            const pairing = app.startPairing('device-1', '000042');
            await flush();
            expect(controller.connects).toHaveLength(1);
            controller.acceptConnect(0);
            await flush();
            expect(controller.pendingSends).toHaveLength(1);
            expect(controller.sent.map((frame) => frame[0])).toEqual([PairingFrameType.REQUEST]);

            const offer = phone.offerFor(controller.sent[0]);
            expect(await app.cancelPairing()).toBe(true);

            // The offer arrives after the cancel while the listener is still live: a
            // cancelled startup must never process it or release `pB`.
            controller.emit('link-1', offer);
            await flush();
            expect(controller.sent.map((frame) => frame[0])).toEqual([PairingFrameType.REQUEST]);
            expect(openHandleCount()).toBe(0);

            // Settlement closes the transport and leaves no pending trust record.
            controller.acceptSend(0);
            expect(await pairing).toBe(false);
            await flush();
            expect(openHandleCount()).toBe(0);
            expect(controller.disconnected).toEqual(['link-1']);
            expect(await trust.repository.peers()).toEqual([]);
        } finally {
            phone.close();
            app.dispose();
        }
    });

    it('dispose_during_pairing_startup_aborts_before_the_first_frame', async () => {
        const { app, controller, trust } = await createPairingApp();
        const pairing = app.startPairing('device-1', '000042');
        await flush();
        expect(controller.connects).toHaveLength(1);

        app.dispose();
        // The tab teardown is inert for the pending transport, which then resolves
        // successfully; the startup must still not install that link or send a frame.
        controller.acceptConnect(0);

        expect(await pairing).toBe(false);
        expect(controller.sent).toEqual([]);
        expect(controller.disconnected).toEqual(['link-1']);
        expect(app.pairingState.finished).toBe(true);
        expect(openHandleCount()).toBe(0);
        expect(await trust.repository.peers()).toEqual([]);
    });

    it('replacement_start_while_pending_leaves_exactly_one_owned_link', async () => {
        const { app, controller, trust } = await createPairingApp();
        try {
            const first = app.startPairing('device-1', '000042');
            await flush();
            expect(controller.connects).toHaveLength(1);

            const second = app.startPairing('device-2', '000043');
            await flush();
            expect(controller.connects).toHaveLength(2);

            // The first startup resolves after being replaced: `start` sees the stale
            // generation right after connect, closes the transport and sends nothing.
            controller.acceptConnect(0);
            expect(await first).toBe(false);
            expect(controller.sent).toEqual([]);
            expect(controller.disconnected).toEqual(['link-1']);
            expect(app.pairingState).toEqual({ phase: 'idle', finished: false });

            // The second startup owns the one live link and sends its request.
            controller.acceptConnect(1);
            expect(await second).toBe(true);
            expect(app.pairingState).toEqual({ phase: 'awaiting_offer', finished: false });
            expect(controller.sent).toHaveLength(1);
            expect(controller.disconnected).toEqual(['link-1']);

            expect(await app.cancelPairing()).toBe(true);
            expect(controller.disconnected).toEqual(['link-1', 'link-2']);
            expect(openHandleCount()).toBe(0);
            expect(await trust.repository.peers()).toEqual([]);
        } finally {
            app.dispose();
        }
    });

    it('code_reference_released_on_every_terminal_path', async () => {
        const success = await setup();
        try {
            // The machine holds the code until the offer lets the core own it.
            expect(success.machine.codeErasedForTest()).toBe(false);
            await toAwaitingPrepare(success);
            expect(success.machine.codeErasedForTest()).toBe(true);
            expect(await success.machine.onFrame(await success.phone.prepareFrame())).toEqual([]);
            await success.machine.commit();
            // The repository verifier is the production activation gate; accept the
            // complete-stage proof this enrollment connection just verified.
            success.trust.bindVerifier(async () => true);
            const ack = await success.machine.onFrame(await success.phone.completeFrame());
            expect(ack).toHaveLength(1);
            expect(success.machine.state).toBe('active');
            expect(success.machine.codeErasedForTest()).toBe(true);
        } finally {
            success.phone.close();
        }

        const wrongCode = await setup({ phoneCode: '111111' });
        try {
            const cA = await toAwaitingCa(wrongCode);
            expect(wrongCode.machine.codeErasedForTest()).toBe(true);
            await wrongCode.machine.onFrame(caFrame(cA));
            expect(wrongCode.machine.failed).toBe(true);
            expect(wrongCode.machine.codeErasedForTest()).toBe(true);
        } finally {
            wrongCode.phone.close();
        }

        const timedOut = await setup();
        try {
            timedOut.machine.start();
            timedOut.clock.advance(30_000);
            expect(await timedOut.machine.onTick()).toBe(true);
            expect(timedOut.machine.codeErasedForTest()).toBe(true);
        } finally {
            timedOut.phone.close();
        }

        const cancelled = await setup();
        try {
            cancelled.machine.start();
            expect(await cancelled.machine.cancel()).toBe(true);
            expect(cancelled.machine.codeErasedForTest()).toBe(true);
        } finally {
            cancelled.phone.close();
        }
    });

    it('link_does_not_route_through_the_session_manager', () => {
        const source = readFileSync(new URL('../../src/pairing/link.ts', import.meta.url), 'utf8');
        expect(source).not.toContain("from '../connector/sessions'");
        expect(source).not.toContain('new SessionManager');
    });
});
