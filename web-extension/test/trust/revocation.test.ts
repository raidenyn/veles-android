import { beforeEach, describe, expect, it, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';

import { IndexedDbIdentityStore, type IdentityStore } from '../../src/trust/identity';
import {
    TrustError,
    TrustStoreError,
    TrustUnavailableError,
    type TrustSnapshot,
} from '../../src/trust/model';
import { ChromeTrustRepository, type AuthenticationVerifier } from '../../src/trust/repository';

let db: IDBFactory;
beforeEach(() => {
    db = new IDBFactory();
});

/** Stores one raw identity record so a later read decodes an invalid identity. */
async function writeRawIdentity(value: unknown): Promise<void> {
    const database = await new Promise<IDBDatabase>((resolve, reject) => {
        const request = db.open('veles-installation-v1', 1);
        request.onupgradeneeded = () => request.result.createObjectStore('identity');
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(new Error('identity database open failed'));
    });
    await new Promise<void>((resolve, reject) => {
        const tx = database.transaction('identity', 'readwrite');
        tx.objectStore('identity').put(value, 'installation');
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(new Error('identity write failed'));
    });
    database.close();
}

function setup(overrides: { keys?: IdentityStore; verifier?: AuthenticationVerifier } = {}) {
    const keys = overrides.keys ?? new IndexedDbIdentityStore(db);
    let value: unknown;
    let failRead = false;
    let failWrite = false;
    const writes: TrustSnapshot[] = [];
    const local = {
        restrict: async () => undefined,
        read: async () => {
            if (failRead) throw new Error('trust payload unreadable');
            return structuredClone(value);
        },
        write: async (next: TrustSnapshot) => {
            if (failWrite) throw new Error('durable write failed');
            writes.push(structuredClone(next));
            value = structuredClone(next);
        },
        clear: async () => {
            value = undefined;
        },
    };
    let elapsed = 0;
    const live = new Set<string>();
    let chain = Promise.resolve();
    const lock = async <T>(operation: () => Promise<T>): Promise<T> => {
        const result = chain.then(operation);
        chain = result.then(
            () => undefined,
            () => undefined,
        );
        return result;
    };
    const verifier: AuthenticationVerifier =
        overrides.verifier ?? (async (_peer, _stage, proof) => proof[0] === 1);
    let storeFailures = 0;
    const make = () => {
        const id = crypto.randomUUID();
        live.add(id);
        return new ChromeTrustRepository(
            keys,
            local,
            lock,
            verifier,
            () => elapsed,
            () => 1000,
            { id, isAlive: async (context) => live.has(context) },
            () => {
                storeFailures += 1;
            },
        );
    };
    return {
        keys,
        local,
        writes,
        make,
        get: () => value as TrustSnapshot,
        failReads: (on = true) => {
            failRead = on;
        },
        failWrites: (on = true) => {
            failWrite = on;
        },
        tick: (n: number) => {
            elapsed = n;
        },
        failures: () => storeFailures,
    };
}

async function candidate() {
    const key = await new IndexedDbIdentityStore(db, crypto.randomUUID()).create();
    return {
        installationId: key.installationId,
        publicKey: key.publicKey,
        label: 'Phone',
        deviceType: 0,
        enrollmentTranscript: 'ab'.repeat(32),
    };
}

/** A peer past `complete`, i.e. `active_activation_pending`, optionally with a hint. */
async function committed(repo: ChromeTrustRepository, hint?: string) {
    const peer = await candidate();
    await repo.addPending(peer);
    await repo.confirm(peer.installationId, 'complete', new Uint8Array([1]));
    if (hint !== undefined) {
        await repo.authenticated(peer.installationId, hint, new Uint8Array([1]));
    }
    return peer;
}

async function active(repo: ChromeTrustRepository, hint?: string) {
    const peer = await committed(repo, hint);
    await repo.confirm(peer.installationId, 'phone_active', new Uint8Array([1]));
    return peer;
}

const ids = (peers: readonly { installationId: string }[]) => peers.map((p) => p.installationId);

describe('trust-store failure classification', () => {
    it('invalidIdentityIsStoreError', async () => {
        // The persisted identity cannot be decoded: a store failure, never a peer rejection.
        await writeRawIdentity({ installationId: 'not-hex' });
        const s = setup();
        const repo = s.make();

        await expect(repo.identity()).rejects.toBeInstanceOf(TrustStoreError);
        await expect(repo.identity()).rejects.toThrow('re-pair required');
        expect(s.failures()).toBe(1);
    });

    it('keysReadFailureIsStoreError', async () => {
        const failingKeys: IdentityStore = {
            read: async () => {
                throw new TrustUnavailableError();
            },
            create: async () => {
                throw new TrustUnavailableError();
            },
            clear: async () => undefined,
        };
        const s = setup({ keys: failingKeys });
        const repo = s.make();

        await expect(repo.identity()).rejects.toBeInstanceOf(TrustStoreError);
        expect(s.failures()).toBe(1);
    });

    it('failingLocalWriteIsStoreError', async () => {
        const s = setup();
        const repo = s.make();
        await repo.identity();
        const peer = await candidate();
        s.failWrites();

        await expect(repo.addPending(peer)).rejects.toBeInstanceOf(TrustStoreError);

        // Same user-facing text as the peer rejection; a store failure is not a silent pass.
        expect(new TrustStoreError().message).toBe(new TrustError().message);
        expect(new TrustStoreError()).toBeInstanceOf(TrustError);
        expect(s.get().peers).toEqual([]);
        expect(s.failures()).toBe(1);
        s.failWrites(false);
        expect(await repo.peers()).toEqual([]);
    });

    it('postOperationValidationFailureIsStoreError', async () => {
        const s = setup();
        const repo = s.make();
        await repo.identity();

        // The operation itself pushes the record; the failed post-operation validation is
        // the store's, so a bad caller-supplied key is a store failure and nothing is written.
        await expect(
            repo.addPending({ ...(await candidate()), publicKey: '04' + '00'.repeat(64) }),
        ).rejects.toBeInstanceOf(TrustStoreError);

        expect(s.get().peers).toEqual([]);
        expect(s.failures()).toBe(1);
    });

    it('missingPeerIsTrustError', async () => {
        const s = setup();
        const repo = s.make();
        await repo.identity();

        for (const operation of [
            () => repo.confirm('09'.repeat(16), 'complete', new Uint8Array([1])),
            () => repo.authenticated('09'.repeat(16), 'winrt:opaque', new Uint8Array([1])),
            () => repo.forgetHint('09'.repeat(16)),
        ]) {
            await expect(operation()).rejects.toBeInstanceOf(TrustError);
            await expect(operation()).rejects.not.toBeInstanceOf(TrustStoreError);
        }
        expect(s.failures()).toBe(0);
    });

    it('onStoreFailureOncePerEpisode', async () => {
        const s = setup();
        const repo = s.make();
        await repo.identity();

        // Several callers observing the same failing store report once for the episode.
        s.failReads();
        await expect(repo.identity()).rejects.toBeInstanceOf(TrustStoreError);
        await expect(repo.peers()).rejects.toBeInstanceOf(TrustStoreError);
        await expect(repo.activePeers()).rejects.toBeInstanceOf(TrustStoreError);
        expect(s.failures()).toBe(1);

        // A successful transaction ends the episode without reporting.
        s.failReads(false);
        await repo.peers();
        expect(s.failures()).toBe(1);

        // The next failure begins a new episode and reports again.
        s.failReads();
        await expect(repo.peers()).rejects.toBeInstanceOf(TrustStoreError);
        expect(s.failures()).toBe(2);
    });

    it('reset ends the failure episode', async () => {
        const s = setup();
        const repo = s.make();
        s.failReads();
        await expect(repo.identity()).rejects.toBeInstanceOf(TrustStoreError);
        expect(s.failures()).toBe(1);

        await repo.reset();

        await expect(repo.identity()).rejects.toBeInstanceOf(TrustStoreError);
        expect(s.failures()).toBe(2);
    });
});

describe('revocation fence', () => {
    it('fencedAddPendingRefusedBeforeDurableWrite', async () => {
        const s = setup();
        const repo = s.make();
        await repo.identity();
        const peer = await candidate();
        const before = s.writes.length;

        // Hold the operation at its one asynchronous step, so the revocation mark is set
        // while the transaction is already past the operation's start.
        const originalDigest = crypto.subtle.digest.bind(crypto.subtle);
        let entered!: () => void;
        const enteredDigest = new Promise<void>((resolve) => {
            entered = resolve;
        });
        let release!: () => void;
        const gate = new Promise<void>((resolve) => {
            release = resolve;
        });
        const digest = vi
            .spyOn(crypto.subtle, 'digest')
            .mockImplementation(async (algorithm, data) => {
                entered();
                await gate;
                return originalDigest(algorithm, data);
            });
        try {
            const adding = repo.addPending(peer);
            await enteredDigest;
            repo.beginRevocation(peer.installationId);
            release();

            // The fence is a peer rejection, and it runs immediately before the write.
            await expect(adding).rejects.toBeInstanceOf(TrustError);
            await expect(adding).rejects.not.toBeInstanceOf(TrustStoreError);
        } finally {
            digest.mockRestore();
        }

        expect(s.writes.length).toBe(before);
        expect(await repo.peers()).toEqual([]);
        expect(s.failures()).toBe(0);
    });

    it('overlappingFencesRefCounted', async () => {
        const s = setup();
        const repo = s.make();
        await repo.identity();
        const peer = await candidate();
        const other = await candidate();

        repo.beginRevocation(peer.installationId);
        repo.beginRevocation(peer.installationId);

        await expect(repo.addPending(peer)).rejects.toBeInstanceOf(TrustError);
        await expect(repo.addPending(peer)).rejects.not.toBeInstanceOf(TrustStoreError);
        // The fence is per id: another peer's pending record is unaffected.
        await repo.addPending(other);

        repo.endRevocation(peer.installationId);
        await expect(repo.addPending(peer)).rejects.toBeInstanceOf(TrustError);

        repo.endRevocation(peer.installationId);
        await repo.addPending(peer);
        expect(ids(await repo.peers()).sort()).toEqual(
            [peer.installationId, other.installationId].sort(),
        );
        expect(s.failures()).toBe(0);
    });
});

describe('unconditional revoke', () => {
    it('revokeWorksInEveryState', async () => {
        const s = setup();
        const repo = s.make();
        const pending = await candidate();
        await repo.addPending(pending);
        const waiting = await committed(repo);
        const connected = await active(repo);
        expect(ids(await repo.peers())).toEqual([
            pending.installationId,
            waiting.installationId,
            connected.installationId,
        ]);

        // Pending: the activation can no longer complete.
        await repo.revoke(pending.installationId);
        expect(ids(await repo.peers())).toEqual([waiting.installationId, connected.installationId]);
        await expect(
            repo.confirm(pending.installationId, 'complete', new Uint8Array([1])),
        ).rejects.toBeInstanceOf(TrustError);

        // Committed waiting for acknowledgement.
        await repo.revoke(waiting.installationId);
        expect(ids(await repo.peers())).toEqual([connected.installationId]);
        await expect(
            repo.confirm(waiting.installationId, 'phone_active', new Uint8Array([1])),
        ).rejects.toBeInstanceOf(TrustError);

        // Active.
        await repo.revoke(connected.installationId);
        expect(await repo.peers()).toEqual([]);
        await expect(
            repo.authenticated(connected.installationId, undefined, new Uint8Array([1])),
        ).rejects.toBeInstanceOf(TrustError);

        // Unconditional: revoking an id that is already gone is a no-op, not a failure.
        await repo.revoke(connected.installationId);
        expect(s.failures()).toBe(0);
    });

    it('revokeRemovesHintAndRejectsLaterHintWrite', async () => {
        const s = setup();
        const repo = s.make();
        const peer = await active(repo, 'winrt:opaque');
        expect(JSON.stringify(s.get())).toContain('winrt:opaque');

        await repo.revoke(peer.installationId);

        // The hint lives on the peer record: no record and no hint survive the revoke.
        expect(s.get().peers).toEqual([]);
        expect(JSON.stringify(s.get())).not.toContain('winrt:opaque');

        // A later hint write for the removed id finds no record and is a peer rejection.
        await expect(
            repo.authenticated(peer.installationId, 'winrt:later', new Uint8Array([1])),
        ).rejects.toBeInstanceOf(TrustError);
        await expect(
            repo.authenticated(peer.installationId, 'winrt:later', new Uint8Array([1])),
        ).rejects.not.toBeInstanceOf(TrustStoreError);
        await expect(repo.forgetHint(peer.installationId)).rejects.toBeInstanceOf(TrustError);
        expect(JSON.stringify(s.get())).not.toContain('winrt:later');
        expect(await repo.peers()).toEqual([]);
        expect(s.failures()).toBe(0);
    });

    it('cancelPendingDoesNotDeleteRecoveredActiveRecord', async () => {
        // The regression for ClientMachine.#cancel: it uses the conditional revokePending,
        // so an abandoned enrollment cannot delete a record a recovery session recovered.
        const s = setup();
        const repo = s.make();
        const abandoned = await candidate();
        await repo.addPending(abandoned);
        expect(await repo.revokePending(abandoned.installationId)).toBe(true);
        expect(await repo.peers()).toEqual([]);

        const recovered = await committed(repo, 'winrt:opaque');
        expect(await repo.revokePending(recovered.installationId)).toBe(false);
        const [kept] = await repo.peers();
        expect(kept.installationId).toBe(recovered.installationId);
        expect(kept.state).toBe('active_activation_pending');
        expect(kept.platformHint).toBe('winrt:opaque');

        // A second cancel for the same id is likewise a no-op, never a delete.
        expect(await repo.revokePending(recovered.installationId)).toBe(false);
        expect((await repo.peers())[0].platformHint).toBe('winrt:opaque');
        expect(s.failures()).toBe(0);
    });
});
