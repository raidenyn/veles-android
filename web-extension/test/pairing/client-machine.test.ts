import { readFileSync } from 'node:fs';
import { afterEach, beforeAll, describe, expect, it } from 'vitest';

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
import { bytes } from '../../src/trust/model';
import type { ChromeTrustRepository } from '../../src/trust/repository';
import { SessionClock, createTestTrust, flush } from '../connector/session-harness';
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

    it('link_does_not_route_through_the_session_manager', () => {
        const source = readFileSync(new URL('../../src/pairing/link.ts', import.meta.url), 'utf8');
        expect(source).not.toContain("from '../connector/sessions'");
        expect(source).not.toContain('new SessionManager');
    });
});
