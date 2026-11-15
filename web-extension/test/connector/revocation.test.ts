import { describe, expect, it } from 'vitest';

import {
    createRevocationCoordinator,
    type RevocationCoordinator,
    type TrustStatus,
} from '../../src/connector/revocation';
import type {
    AuthenticatedSession,
    RemovalResult,
    SessionManager,
} from '../../src/connector/sessions';
import type { RemovalOperations } from '../../src/session/removal';
import { SessionClass } from '../../src/session/wire';
import { TrustStoreError, type TrustedPeer } from '../../src/trust/model';
import type { ChromeTrustRepository } from '../../src/trust/repository';

// OTP-11 Task 10: the Chrome revocation coordinator (design D2/D3/D5/D6/D8) over recording
// doubles. The repository and the session manager write into one event list, so every ordering
// assertion is real: fence, delete, terminate, sweep, release for a local revoke; the detached
// handler-driven finish; asynchronous, non-destructive fail-closed; reset before any deletion;
// and the read-only peer summary. Nothing sleeps and nothing reads the wall clock.

const FENCE = 'fence';
const DELETE = 'delete';
const CLOSE = 'close';
const SWEEP = 'sweep';
const RELEASE = 'release';
const CLOSE_ALL = 'close-all';
const RESET = 'reset';

async function microFlush(times = 16): Promise<void> {
    for (let index = 0; index < times; index += 1) await Promise.resolve();
}

/** The revocation stages only: the store-failure escalation's own teardown is asserted separately. */
function withoutFailClosed(events: string[]): string[] {
    return events.filter((event) => event !== CLOSE_ALL);
}

function peerRecord(installationId: string, overrides: Partial<TrustedPeer> = {}): TrustedPeer {
    return {
        installationId,
        publicKey: `04${'11'.repeat(64)}`,
        label: 'Pixel test phone',
        deviceType: 0,
        enrollmentTranscript: 'ab'.repeat(32),
        fingerprint: 'cd'.repeat(32),
        state: 'active',
        createdAtMillis: 1_000,
        lastSeenMillis: 5_000,
        pendingContext: null,
        pendingSince: null,
        ...overrides,
    };
}

function openSession(
    peerId: string,
    handle: string,
    sessionClass: SessionClass = SessionClass.FULL,
): AuthenticatedSession {
    return {
        handle,
        peerId,
        sessionClass,
        sessionIdPrefix: new Uint8Array(8),
    };
}

/**
 * A recording trust repository. Its first `revoke` is the delete and every later one is the
 * final sweep, so the two stages are separately failable and separately ordered. A store
 * failure is reported once through the composition root's callback and then thrown, exactly as
 * `ChromeTrustRepository` does at its choke point.
 */
class RecordingRepository {
    readonly events: string[];
    readonly fence = new Map<string, number>();
    readonly beginDepths: number[] = [];
    readonly endDepths: number[] = [];
    records: TrustedPeer[] = [];
    revokes = 0;
    resetCalls = 0;
    deleteFailure: Error | null = null;
    sweepFailure: Error | null = null;
    resetFailure: Error | null = null;
    deleteGate: Promise<void> | null = null;
    sweepGate: Promise<void> | null = null;
    peersFailure: Error | null = null;
    onStoreFailure: (() => void) | null = null;

    constructor(events: string[]) {
        this.events = events;
    }

    beginRevocation(peerId: string): void {
        const depth = (this.fence.get(peerId) ?? 0) + 1;
        this.fence.set(peerId, depth);
        this.beginDepths.push(depth);
        this.events.push(FENCE);
    }

    endRevocation(peerId: string): void {
        const depth = (this.fence.get(peerId) ?? 1) - 1;
        if (depth > 0) this.fence.set(peerId, depth);
        else this.fence.delete(peerId);
        this.endDepths.push(Math.max(depth, 0));
        this.events.push(RELEASE);
    }

    async revoke(peerId: string): Promise<void> {
        this.revokes += 1;
        if (this.revokes === 1) {
            this.events.push(DELETE);
            if (this.deleteGate !== null) await this.deleteGate;
            if (this.deleteFailure !== null) throw this.reported(this.deleteFailure);
        } else {
            this.events.push(SWEEP);
            if (this.sweepGate !== null) await this.sweepGate;
            if (this.sweepFailure !== null) throw this.reported(this.sweepFailure);
        }
        this.records = this.records.filter((peer) => peer.installationId !== peerId);
    }

    async peers(): Promise<TrustedPeer[]> {
        if (this.peersFailure !== null) throw this.reported(this.peersFailure);
        return this.records.slice();
    }

    async reset(): Promise<void> {
        this.events.push(RESET);
        this.resetCalls += 1;
        if (this.resetFailure !== null) throw this.resetFailure;
        this.records = [];
    }

    async identity(): Promise<never> {
        throw new Error('the coordinator never reads the installation identity');
    }

    private reported(failure: Error): Error {
        if (failure instanceof TrustStoreError) this.onStoreFailure?.();
        return failure;
    }
}

/** The session owner as the coordinator sees it: two non-destructive teardowns and the request. */
class RecordingSessions {
    readonly events: string[];
    readonly closedPeers: string[] = [];
    readonly removals: string[] = [];
    closeAllCalls = 0;
    closeFailure: Error | null = null;
    closeAllFailure: Error | null = null;
    closeGate: Promise<void> | null = null;
    closeAllGate: Promise<void> | null = null;
    removal: RemovalResult = 'kept-not-connected';
    openSessions: AuthenticatedSession[] = [];

    constructor(events: string[]) {
        this.events = events;
    }

    async closePeer(peerId: string): Promise<void> {
        this.events.push(CLOSE);
        this.closedPeers.push(peerId);
        if (this.closeGate !== null) await this.closeGate;
        if (this.closeFailure !== null) throw this.closeFailure;
    }

    async closeAll(): Promise<void> {
        this.events.push(CLOSE_ALL);
        this.closeAllCalls += 1;
        if (this.closeAllGate !== null) await this.closeAllGate;
        if (this.closeAllFailure !== null) throw this.closeAllFailure;
    }

    requestRemoval(peerId: string): Promise<RemovalResult> {
        this.removals.push(peerId);
        return Promise.resolve(this.removal);
    }

    authenticatedSessions(): AuthenticatedSession[] {
        return this.openSessions;
    }
}

interface Harness {
    events: string[];
    repo: RecordingRepository;
    sessions: RecordingSessions;
    coordinator: RevocationCoordinator & RemovalOperations;
    statuses: TrustStatus[];
    cleaned: string[];
}

function createHarness(options: { historyCleanup?: (peerId: string) => void } = {}): Harness {
    const events: string[] = [];
    const repo = new RecordingRepository(events);
    const sessions = new RecordingSessions(events);
    const statuses: TrustStatus[] = [];
    const cleaned: string[] = [];
    const coordinator = createRevocationCoordinator({
        repo: repo as unknown as ChromeTrustRepository,
        sessions: sessions as unknown as SessionManager,
        onStatus: (status) => statuses.push(status),
        historyCleanup: options.historyCleanup ?? ((peerId) => cleaned.push(peerId)),
    });
    // The composition root wires the repository's store-failure callback to fail-closed.
    repo.onStoreFailure = () => coordinator.failClosed();
    return { events, repo, sessions, coordinator, statuses, cleaned };
}

describe('revocation coordinator: local revoke', () => {
    it('orders the fence, delete, terminate, sweep and release', async () => {
        const harness = createHarness();
        const peerId = '11'.repeat(16);
        harness.repo.records.push(peerRecord(peerId));

        await harness.coordinator.revoke(peerId);

        expect(harness.events).toEqual([FENCE, DELETE, CLOSE, SWEEP, RELEASE]);
        expect(harness.sessions.closedPeers).toEqual([peerId]);
        expect(harness.repo.records).toEqual([]);
        expect(harness.repo.beginDepths).toEqual([1]);
        expect(harness.repo.endDepths).toEqual([0]);
        expect(harness.statuses).toEqual([]);
    });

    it('terminates the peer even when the delete fails, then releases the fence', async () => {
        const harness = createHarness();
        const peerId = '11'.repeat(16);
        harness.repo.deleteFailure = new TrustStoreError();

        await expect(harness.coordinator.revoke(peerId)).rejects.toBeInstanceOf(TrustStoreError);

        expect(harness.sessions.closedPeers).toEqual([peerId]);
        expect(withoutFailClosed(harness.events)).toEqual([FENCE, DELETE, CLOSE, SWEEP, RELEASE]);
        expect(harness.repo.endDepths).toEqual([0]);
        // The repository reported the store failure, which escalated to fail-closed.
        expect(harness.statuses).toEqual(['re-pair-required']);
    });

    it('reports a peer rejection to the caller and still releases the fence', async () => {
        const harness = createHarness();
        const peerId = '11'.repeat(16);
        harness.repo.deleteFailure = new Error('record missing');

        await expect(harness.coordinator.revoke(peerId)).rejects.toThrow('record missing');

        expect(harness.sessions.closedPeers).toEqual([peerId]);
        expect(harness.repo.endDepths).toEqual([0]);
        expect(harness.statuses).toEqual([]);
    });

    it('sweeps and releases the fence when the termination fails', async () => {
        const harness = createHarness();
        const peerId = '11'.repeat(16);
        harness.sessions.closeFailure = new Error('transport refuses to close');

        await harness.coordinator.revoke(peerId);

        expect(harness.events).toEqual([FENCE, DELETE, CLOSE, SWEEP, RELEASE]);
        expect(harness.repo.endDepths).toEqual([0]);
    });

    it('releases the fence when the sweep fails', async () => {
        const harness = createHarness();
        const peerId = '11'.repeat(16);
        harness.repo.sweepFailure = new TrustStoreError();

        await harness.coordinator.revoke(peerId);

        expect(harness.repo.endDepths).toEqual([0]);
        expect(harness.statuses).toEqual(['re-pair-required']);
        expect(withoutFailClosed(harness.events)).toEqual([FENCE, DELETE, CLOSE, SWEEP, RELEASE]);
    });

    it('keeps the fence while the delete is still in flight', async () => {
        const harness = createHarness();
        const peerId = '11'.repeat(16);
        let release!: () => void;
        harness.repo.deleteGate = new Promise<void>((resolve) => {
            release = resolve;
        });

        const revocation = harness.coordinator.revoke(peerId);
        await microFlush();

        expect(harness.repo.fence.get(peerId)).toBe(1);
        expect(harness.repo.endDepths).toEqual([]);
        expect(harness.events).toEqual([FENCE, DELETE]);

        release();
        await revocation;

        expect(harness.repo.fence.size).toBe(0);
        expect(harness.events).toEqual([FENCE, DELETE, CLOSE, SWEEP, RELEASE]);
    });

    it('reference-counts overlapping revocations of one peer', async () => {
        const harness = createHarness();
        const peerId = '11'.repeat(16);
        let release!: () => void;
        harness.repo.deleteGate = new Promise<void>((resolve) => {
            release = resolve;
        });

        const first = harness.coordinator.revoke(peerId);
        await microFlush();
        // The second revocation must still take the fence, not wait for the first.
        const second = harness.coordinator.revoke(peerId);
        await microFlush();
        expect(harness.repo.beginDepths).toEqual([1, 2]);

        release();
        await first;
        await second;

        expect(harness.repo.fence.size).toBe(0);
        expect(harness.repo.endDepths).toEqual([1, 0]);
    });

    it('calls the history cleanup with the installation id and no other value', async () => {
        const harness = createHarness();
        const peerId = '11'.repeat(16);

        await harness.coordinator.revoke(peerId);

        expect(harness.cleaned).toEqual([peerId]);
    });

    it('never fails a removal whose history cleanup throws', async () => {
        const harness = createHarness({
            historyCleanup: () => {
                throw new Error('history cleanup unavailable');
            },
        });
        const peerId = '11'.repeat(16);

        await harness.coordinator.revoke(peerId);

        expect(harness.repo.records).toEqual([]);
        expect(harness.repo.endDepths).toEqual([0]);
    });

    it('never fails a removal whose history cleanup throws a cancellation-like value', async () => {
        const harness = createHarness({
            historyCleanup: () => {
                // A consumer's own cancellation must stay inside the extension point.
                throw Object.assign(new Error('cancelled'), { name: 'CancellationError' });
            },
        });
        const peerId = '11'.repeat(16);

        await harness.coordinator.revoke(peerId);

        expect(harness.events).toEqual([FENCE, DELETE, CLOSE, SWEEP, RELEASE]);
        expect(harness.repo.endDepths).toEqual([0]);
    });
});

describe('revocation coordinator: handler-driven removal', () => {
    it('starts the detached finish before the delete and keeps the fence past its return', async () => {
        const harness = createHarness();
        const peerId = '11'.repeat(16);
        let release!: () => void;
        harness.sessions.closeGate = new Promise<void>((resolve) => {
            release = resolve;
        });

        await harness.coordinator.deleteFenced(peerId);

        // The delete returned while the finish is still waiting behind the caller's step.
        expect(harness.events).toEqual([FENCE, CLOSE, DELETE]);
        expect(harness.repo.fence.get(peerId)).toBe(1);
        expect(harness.repo.endDepths).toEqual([]);

        release();
        await microFlush();

        expect(harness.events).toEqual([FENCE, CLOSE, DELETE, SWEEP, RELEASE]);
        expect(harness.repo.fence.size).toBe(0);
        expect(harness.repo.endDepths).toEqual([0]);
    });

    it('releases the fence from the detached finish after a failed delete', async () => {
        const harness = createHarness();
        const peerId = '11'.repeat(16);
        harness.repo.deleteFailure = new TrustStoreError();

        await expect(harness.coordinator.deleteFenced(peerId)).rejects.toBeInstanceOf(
            TrustStoreError,
        );

        expect(harness.sessions.closedPeers).toEqual([peerId]);
        await microFlush();
        expect(withoutFailClosed(harness.events)).toEqual([FENCE, CLOSE, DELETE, SWEEP, RELEASE]);
        expect(harness.repo.endDepths).toEqual([0]);
        expect(harness.statuses).toEqual(['re-pair-required']);
    });

    it('releases the fence when the termination of the detached finish fails', async () => {
        const harness = createHarness();
        const peerId = '11'.repeat(16);
        harness.sessions.closeFailure = new Error('transport refuses to close');

        await harness.coordinator.deleteFenced(peerId);
        await microFlush();

        expect(harness.events).toEqual([FENCE, CLOSE, DELETE, SWEEP, RELEASE]);
        expect(harness.repo.endDepths).toEqual([0]);
    });

    it('runs the history cleanup once for the removed peer', async () => {
        const harness = createHarness();
        const peerId = '11'.repeat(16);

        await harness.coordinator.deleteFenced(peerId);
        await microFlush();

        expect(harness.cleaned).toEqual([peerId]);
        expect(harness.repo.records).toEqual([]);
    });
});

describe('revocation coordinator: fail-closed', () => {
    it('reports one status, terminates asynchronously and deletes nothing', async () => {
        const harness = createHarness();
        harness.repo.records.push(peerRecord('11'.repeat(16)));
        harness.sessions.openSessions.push(openSession('11'.repeat(16), 'c1'));

        harness.coordinator.failClosed();

        expect(harness.statuses).toEqual(['re-pair-required']);
        expect(harness.events).toEqual([]);
        expect(harness.repo.fence.size).toBe(0);
        expect(harness.repo.records).toHaveLength(1);

        await microFlush();

        expect(harness.events).toEqual([CLOSE_ALL]);
        expect(harness.sessions.closeAllCalls).toBe(1);
        expect(harness.repo.revokes).toBe(0);
        expect(harness.repo.resetCalls).toBe(0);
        expect(harness.repo.records).toHaveLength(1);
    });

    it('does not await a failing teardown and never throws from the callback', async () => {
        const harness = createHarness();
        harness.sessions.closeAllFailure = new Error('transport refuses to close');

        expect(() => harness.coordinator.failClosed()).not.toThrow();
        await microFlush();

        expect(harness.sessions.closeAllCalls).toBe(1);
        expect(harness.statuses).toEqual(['re-pair-required']);
    });
});

describe('revocation coordinator: reset', () => {
    it('terminates every session and link before the repository reset', async () => {
        const harness = createHarness();
        harness.repo.records.push(peerRecord('11'.repeat(16)));

        await harness.coordinator.reset();

        expect(harness.events).toEqual([CLOSE_ALL, RESET]);
        expect(harness.repo.resetCalls).toBe(1);
        expect(harness.sessions.closeAllCalls).toBe(1);
        expect(harness.repo.revokes).toBe(0);
        expect(harness.repo.fence.size).toBe(0);
        expect(harness.statuses).toEqual([]);
    });

    it('still resets after a failing termination and rethrows a reset failure', async () => {
        const harness = createHarness();
        harness.sessions.closeAllFailure = new Error('transport refuses to close');
        harness.repo.resetFailure = new TrustStoreError();

        await expect(harness.coordinator.reset()).rejects.toBeInstanceOf(TrustStoreError);

        expect(harness.repo.resetCalls).toBe(1);
        expect(harness.events).toEqual([CLOSE_ALL, RESET]);
    });
});

describe('revocation coordinator: mutual removal', () => {
    it('asks the session owner for the removal and returns its outcome', async () => {
        const harness = createHarness();
        harness.sessions.removal = 'removed';

        await expect(harness.coordinator.removeMutually('11'.repeat(16))).resolves.toBe('removed');
        expect(harness.sessions.removals).toEqual(['11'.repeat(16)]);
        // The requester never deletes: only the validated response's handler does.
        expect(harness.repo.revokes).toBe(0);
        expect(harness.repo.fence.size).toBe(0);
    });
});

describe('revocation coordinator: peer summary', () => {
    it('lists every record fresh with a session kind and no secrets or hints', async () => {
        const harness = createHarness();
        const active = peerRecord('11'.repeat(16), {
            label: 'Pixel',
            fingerprint: 'ee'.repeat(32),
            lastSeenMillis: 7_000,
        });
        const pending = peerRecord('22'.repeat(16), {
            state: 'pending',
            lastSeenMillis: null,
            pendingContext: '3f0a5c48-5f34-4c4d-9a51-13d2b30e0a11',
            pendingSince: 10,
        });
        harness.repo.records.push(active, pending);
        harness.sessions.openSessions.push(openSession(active.installationId, 'c1'));

        const summaries = await harness.coordinator.peers();

        expect(summaries).toHaveLength(2);
        const listed = summaries.find(
            (summary) => summary.installationId === active.installationId,
        );
        expect(listed).toEqual({
            installationId: active.installationId,
            label: active.label,
            fingerprint: active.fingerprint,
            deviceType: active.deviceType,
            state: 'active',
            createdAtMillis: active.createdAtMillis,
            lastSeenMillis: active.lastSeenMillis,
            session: 'full',
        });
        const other = summaries.find(
            (summary) => summary.installationId === pending.installationId,
        );
        expect(other?.session).toBe('none');
        expect(other?.state).toBe('pending');
        expect(other?.lastSeenMillis).toBeNull();

        // The summary is computed fresh on every call.
        harness.repo.records = [pending];
        harness.sessions.openSessions = [];
        const next = await harness.coordinator.peers();
        expect(next.map((summary) => summary.installationId)).toEqual([pending.installationId]);

        const serialized = JSON.stringify([...summaries, ...next]);
        expect(serialized).not.toContain(active.publicKey);
        expect(serialized).not.toContain(active.enrollmentTranscript);
        expect(serialized).not.toContain('platformHint');
        expect(serialized).not.toContain('pendingContext');
        expect(serialized).not.toContain('handle');
        expect(serialized).not.toContain('publicKey');
    });

    it('maps each live session class to a kind, and full-pending-peer is never full', async () => {
        const harness = createHarness();
        const full = peerRecord('11'.repeat(16));
        const recovery = peerRecord('22'.repeat(16), { state: 'active_activation_pending' });
        const pendingPeer = peerRecord('33'.repeat(16));
        const idle = peerRecord('44'.repeat(16), { state: 'pending', pendingContext: 'ctx' });
        harness.repo.records.push(full, recovery, pendingPeer, idle);
        harness.sessions.openSessions.push(
            openSession(full.installationId, 'c1', SessionClass.FULL),
            openSession(recovery.installationId, 'c2', SessionClass.ACTIVATION_RECOVERY),
            // A full-pending-peer generation opens the session but cannot carry a removal record.
            openSession(pendingPeer.installationId, 'c3', SessionClass.FULL_PENDING_PEER),
        );

        const kinds = new Map(
            (await harness.coordinator.peers()).map((summary) => [
                summary.installationId,
                summary.session,
            ]),
        );

        expect(kinds.get(full.installationId)).toBe('full');
        expect(kinds.get(recovery.installationId)).toBe('recovery');
        expect(kinds.get(pendingPeer.installationId)).toBe('recovery');
        expect(kinds.get(idle.installationId)).toBe('none');
    });

    it('reports full when the peer also has another live class', async () => {
        const harness = createHarness();
        const peerId = '11'.repeat(16);
        harness.repo.records.push(peerRecord(peerId));
        harness.sessions.openSessions.push(
            openSession(peerId, 'c1', SessionClass.ACTIVATION_RECOVERY),
            openSession(peerId, 'c2', SessionClass.FULL),
        );

        const summaries = await harness.coordinator.peers();

        expect(summaries[0]?.session).toBe('full');
    });

    it('fails closed through the coordinator when a store read fails', async () => {
        const harness = createHarness();
        harness.repo.peersFailure = new TrustStoreError();

        await expect(harness.coordinator.peers()).rejects.toBeInstanceOf(TrustStoreError);
        await microFlush();

        expect(harness.statuses).toEqual(['re-pair-required']);
        expect(harness.sessions.closeAllCalls).toBe(1);
        expect(harness.repo.revokes).toBe(0);
    });
});
