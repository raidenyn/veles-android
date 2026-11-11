import { describe, expect, it, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';

import {
    chooseReconnectTarget,
    hintsFromPeers,
    repositoryHintSource,
    type PeerHint,
} from '../../src/connector/hints';
import {
    RECONNECT_DELAYS_MS,
    RECONNECT_MAX_ATTEMPTS,
    ReconnectPolicy,
    type ReconnectAttempt,
    type ReconnectSuppressionReason,
} from '../../src/connector/reconnect';
import { IndexedDbIdentityStore } from '../../src/trust/identity';
import type { TrustSnapshot } from '../../src/trust/model';
import { ChromeTrustRepository } from '../../src/trust/repository';

class FakeClock {
    now = 0;
    private nextId = 1;
    private readonly tasks = new Map<number, { due: number; run: () => void }>();

    readonly setTimeout = (fn: () => void, ms: number): number => {
        const id = this.nextId;
        this.nextId += 1;
        this.tasks.set(id, { due: this.now + ms, run: fn });
        return id;
    };

    readonly clearTimeout = (handle: unknown): void => {
        if (typeof handle === 'number') this.tasks.delete(handle);
    };

    advance(ms: number): void {
        this.now += ms;
        for (const [id, task] of [...this.tasks]) {
            if (task.due <= this.now) {
                this.tasks.delete(id);
                task.run();
            }
        }
    }

    get pendingTimers(): number {
        return this.tasks.size;
    }
}

async function flush(): Promise<void> {
    await Promise.resolve();
    await Promise.resolve();
}

const peer = (peerId: string, hint: string, label = 'Phone'): PeerHint => ({
    peerId,
    label,
    hint,
});

describe('trust-scoped hints', () => {
    it('selects search when there are no active hints', () => {
        expect(chooseReconnectTarget([])).toEqual({ kind: 'search', reason: 'none' });
    });

    it('selects the one active peer hint', () => {
        expect(chooseReconnectTarget([peer('p1', 'winrt:one')])).toEqual({
            kind: 'hint',
            peerId: 'p1',
            hint: 'winrt:one',
        });
    });

    it('treats two different hints as ambiguous rather than choosing a peer', () => {
        expect(chooseReconnectTarget([peer('p1', 'winrt:one'), peer('p2', 'winrt:two')])).toEqual({
            kind: 'search',
            reason: 'ambiguous',
        });
    });

    it('treats duplicate hints from different peers as ambiguous as well', () => {
        expect(chooseReconnectTarget([peer('p1', 'same'), peer('p2', 'same')])).toEqual({
            kind: 'search',
            reason: 'ambiguous',
        });
    });

    it('ignores empty, control-character, oversized and malformed hints', () => {
        const peers = [
            peer('', 'winrt:no-peer'),
            peer('p1', ''),
            peer('p2', 'line\nbreak'),
            peer('p3', 'x'.repeat(257)),
            { peerId: 'p4', label: 'Phone' } as unknown as PeerHint,
        ];
        expect(hintsFromPeers(peers)).toEqual([]);
        expect(chooseReconnectTarget(peers)).toEqual({ kind: 'search', reason: 'invalid' });
    });

    it('accepts a valid UTF-8 hint at the byte ceiling and trims only for display', () => {
        const hint = 'é'.repeat(128);
        const valid = hintsFromPeers([peer('p1', hint, '  Pixel  ')]);
        expect(valid).toEqual([{ peerId: 'p1', label: 'Pixel', hint }]);
    });

    it('calls only activePeers on an injected repository object', async () => {
        const activePeers = vi.fn(async () => [
            {
                installationId: 'p1',
                label: 'Pixel',
                platformHint: 'winrt:opaque',
            },
            {
                installationId: 'p2',
                label: 'No hint',
            },
        ]);
        const authenticated = vi.fn(async () => {
            throw new Error('must not authenticate');
        });
        const write = vi.fn();
        const repository = { activePeers, authenticated, write };
        const source = repositoryHintSource(repository);

        expect(await source.activePeers()).toEqual([
            { peerId: 'p1', label: 'Pixel', hint: 'winrt:opaque' },
        ]);
        expect(activePeers).toHaveBeenCalledTimes(1);
        expect(authenticated).not.toHaveBeenCalled();
        expect(write).not.toHaveBeenCalled();
    });

    it('reads the real repository without authentication or connector hint mutation', async () => {
        const keys = new IndexedDbIdentityStore(new IDBFactory());
        let stored: TrustSnapshot | undefined;
        const writes: TrustSnapshot[] = [];
        const local = {
            restrict: vi.fn(async () => undefined),
            read: async () => structuredClone(stored),
            write: async (snapshot: TrustSnapshot) => {
                writes.push(structuredClone(snapshot));
                stored = structuredClone(snapshot);
            },
            clear: async () => {
                stored = undefined;
            },
        };
        let chain = Promise.resolve();
        const lock = async <T>(operation: () => Promise<T>): Promise<T> => {
            const result = chain.then(operation);
            chain = result.then(
                () => undefined,
                () => undefined,
            );
            return result;
        };
        const verifier = vi.fn(
            async (_peer: unknown, _stage: string, proof: Uint8Array) => proof[0] === 1,
        );
        const repository = new ChromeTrustRepository(
            keys,
            local,
            lock,
            verifier,
            () => 0,
            () => 1000,
            { id: crypto.randomUUID(), isAlive: async () => true },
        );

        const peerKey = await new IndexedDbIdentityStore(
            new IDBFactory(),
            crypto.randomUUID(),
        ).create();
        const peer = {
            installationId: peerKey.installationId,
            publicKey: peerKey.publicKey,
            label: 'Pixel',
            deviceType: 0,
            enrollmentTranscript: 'ab'.repeat(32),
        };
        await repository.addPending(peer);
        await repository.confirm(peer.installationId, 'complete', new Uint8Array([1]));
        await repository.confirm(peer.installationId, 'phone_active', new Uint8Array([1]));
        await repository.authenticated(peer.installationId, 'winrt:opaque', new Uint8Array([1]));
        verifier.mockClear();
        writes.length = 0;

        const source = repositoryHintSource(repository);
        expect(await source.activePeers()).toEqual([
            { peerId: peer.installationId, label: 'Pixel', hint: 'winrt:opaque' },
        ]);
        expect(verifier).not.toHaveBeenCalled();
        expect(local.restrict).toHaveBeenCalled();
        // The real read path performs repository maintenance writes; the
        // security property is that no authentication, new peer, proof or
        // connector hint mutation occurred.
        expect(writes.length).toBeGreaterThan(0);
        for (const snapshot of writes) {
            expect(snapshot.peers).toHaveLength(1);
            expect(snapshot.peers[0]?.installationId).toBe(peer.installationId);
            expect(snapshot.peers[0]?.platformHint).toBe('winrt:opaque');
            expect(snapshot.peers[0]?.state).toBe('active');
        }
    });
});

function makePolicy(
    clock: FakeClock,
    onAttempt: (attempt: ReconnectAttempt) => boolean | Promise<boolean>,
): ReconnectPolicy {
    return new ReconnectPolicy({
        setTimeout: clock.setTimeout,
        clearTimeout: clock.clearTimeout,
        onAttempt,
    });
}

describe('bounded reconnect policy', () => {
    it('uses exactly three attempts with 1 s, 2 s and 4 s delays', async () => {
        const clock = new FakeClock();
        const attempts: ReconnectAttempt[] = [];
        const policy = makePolicy(clock, (attempt) => {
            attempts.push(attempt);
            return false;
        });

        expect(policy.schedule('host', { kind: 'search', reason: 'none' })).toBe(true);
        expect(RECONNECT_DELAYS_MS).toEqual([1_000, 2_000, 4_000]);

        clock.advance(999);
        await flush();
        expect(attempts).toHaveLength(0);
        clock.advance(1);
        await flush();
        expect(attempts.map((attempt) => attempt.attempt)).toEqual([1]);

        clock.advance(1_999);
        await flush();
        expect(attempts).toHaveLength(1);
        clock.advance(1);
        await flush();
        expect(attempts.map((attempt) => attempt.attempt)).toEqual([1, 2]);

        clock.advance(3_999);
        await flush();
        expect(attempts).toHaveLength(2);
        clock.advance(1);
        await flush();
        expect(attempts.map((attempt) => attempt.attempt)).toEqual([1, 2, 3]);
        expect(policy.attemptsFor('host')).toBe(RECONNECT_MAX_ATTEMPTS);
        expect(policy.isScheduled('host')).toBe(false);

        clock.advance(60_000);
        await flush();
        expect(attempts).toHaveLength(3);
    });

    it('resets a key to a fresh allowance after a successful attempt', async () => {
        const clock = new FakeClock();
        const attempts: ReconnectAttempt[] = [];
        const policy = makePolicy(clock, (attempt) => {
            attempts.push(attempt);
            return attempt.attempt === 2;
        });

        policy.schedule('host', { kind: 'search', reason: 'none' });
        clock.advance(1_000);
        await flush();
        clock.advance(2_000);
        await flush();
        expect(attempts.map((attempt) => attempt.attempt)).toEqual([1, 2]);
        expect(policy.attemptsFor('host')).toBe(0);
        expect(policy.isScheduled('host')).toBe(false);

        expect(policy.schedule('host', { kind: 'hint', peerId: 'p1', hint: 'h' })).toBe(true);
        clock.advance(1_000);
        await flush();
        expect(attempts.map((attempt) => attempt.attempt)).toEqual([1, 2, 1]);
    });

    it('isolates attempts and success per peer', async () => {
        const clock = new FakeClock();
        const attempts: ReconnectAttempt[] = [];
        const policy = makePolicy(clock, (attempt) => {
            attempts.push(attempt);
            return attempt.key === 'peer-a' && attempt.attempt === 1;
        });

        policy.schedule('peer-a', { kind: 'search', reason: 'none' });
        policy.schedule('peer-b', { kind: 'search', reason: 'none' });
        clock.advance(1_000);
        await flush();
        expect(policy.attemptsFor('peer-a')).toBe(0);
        expect(policy.attemptsFor('peer-b')).toBe(1);

        clock.advance(2_000);
        await flush();
        expect(attempts.filter((attempt) => attempt.key === 'peer-a')).toHaveLength(1);
        expect(attempts.filter((attempt) => attempt.key === 'peer-b')).toHaveLength(2);
    });

    it.each<ReconnectSuppressionReason>([
        'intentional_close',
        'shutdown',
        'incompatible_api',
        'missing_host',
    ])('suppresses pending and future recovery after %s', async (reason) => {
        const clock = new FakeClock();
        const attempts: ReconnectAttempt[] = [];
        const policy = makePolicy(clock, (attempt) => {
            attempts.push(attempt);
            return false;
        });

        policy.schedule('host', { kind: 'search', reason: 'none' });
        policy.schedule('peer', { kind: 'search', reason: 'none' });
        policy.suppress(reason);
        expect(policy.suppressed).toBe(true);
        expect(policy.suppressionReason).toBe(reason);
        expect(policy.schedule('later', { kind: 'search', reason: 'none' })).toBe(false);

        clock.advance(60_000);
        await flush();
        expect(attempts).toEqual([]);
        expect(clock.pendingTimers).toBe(0);
    });

    it('cancels only the user-disconnected peer and leaves other peers recoverable', async () => {
        const clock = new FakeClock();
        const attempts: ReconnectAttempt[] = [];
        const policy = makePolicy(clock, (attempt) => {
            attempts.push(attempt);
            return false;
        });

        policy.schedule('peer-a', { kind: 'search', reason: 'none' });
        policy.schedule('peer-b', { kind: 'search', reason: 'none' });
        policy.cancel('peer-a', 'user_disconnect');
        expect(policy.isSuppressed('peer-a')).toBe(true);
        expect(policy.isSuppressed('peer-b')).toBe(false);

        clock.advance(1_000);
        await flush();
        expect(attempts.filter((attempt) => attempt.key === 'peer-a')).toEqual([]);
        expect(attempts.filter((attempt) => attempt.key === 'peer-b')).toHaveLength(1);

        clock.advance(2_000);
        await flush();
        expect(attempts.filter((attempt) => attempt.key === 'peer-b')).toHaveLength(2);
    });

    it('BLE success with failed auth consumes attempts until the bound', async () => {
        const clock = new FakeClock();
        const attempts: number[] = [];
        // Each attempt reaches the transport level (a BLE connect would have
        // succeeded), but authentication fails, so it is not a success and the
        // three-attempt allowance is exhausted.
        const policy = makePolicy(clock, (attempt) => {
            attempts.push(attempt.attempt);
            return false;
        });

        policy.schedule('peer:p1', { kind: 'hint', peerId: 'p1', hint: 'winrt:one' });
        clock.advance(1_000);
        await flush();
        expect(attempts).toEqual([1]);
        clock.advance(2_000);
        await flush();
        expect(attempts).toEqual([1, 2]);
        clock.advance(4_000);
        await flush();
        expect(attempts).toEqual([1, 2, 3]);
        expect(policy.isExhausted('peer:p1')).toBe(true);
        expect(clock.pendingTimers).toBe(0);

        clock.advance(60_000);
        await flush();
        expect(attempts).toEqual([1, 2, 3]);
    });

    it('does nothing when a late timer or late async result arrives after intentional close', async () => {
        const clock = new FakeClock();
        const attempts: ReconnectAttempt[] = [];
        let resolveAttempt: ((value: boolean) => void) | undefined;
        const policy = makePolicy(clock, (attempt) => {
            attempts.push(attempt);
            return new Promise<boolean>((resolve) => {
                resolveAttempt = resolve;
            });
        });

        policy.schedule('host', { kind: 'search', reason: 'none' });
        clock.advance(1_000);
        await flush();
        expect(attempts).toHaveLength(1);

        policy.dispose();
        resolveAttempt?.(false);
        await flush();
        expect(clock.pendingTimers).toBe(0);

        // A captured stale callback cannot arm another attempt either.
        clock.advance(60_000);
        await flush();
        expect(attempts).toHaveLength(1);
    });
});
