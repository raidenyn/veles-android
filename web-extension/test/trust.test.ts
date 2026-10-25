import { beforeEach, describe, expect, it } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import { IndexedDbIdentityStore, validateIdentity } from '../src/trust/identity';
import { ChromeTrustRepository } from '../src/trust/repository';
import type { TrustSnapshot } from '../src/trust/model';
import { ChromeLocalStore, TRUST_STORAGE_KEY } from '../src/trust/storage';
import { vi } from 'vitest';

let db: IDBFactory;
beforeEach(() => {
    db = new IDBFactory();
});

describe('installation identity', () => {
    it('persists a non-exportable signing key and stable random installation ID', async () => {
        const store = new IndexedDbIdentityStore(db);
        expect(await store.read()).toBeUndefined();
        const first = await store.create();
        expect(first.installationId).toMatch(/^[0-9a-f]{32}$/);
        await expect(crypto.subtle.exportKey('pkcs8', first.privateKey)).rejects.toThrow();
        const loaded = await new IndexedDbIdentityStore(db).read();
        expect(loaded?.installationId).toBe(first.installationId);
        expect(loaded?.publicKey).toBe(first.publicKey);
        await validateIdentity(loaded);
        await expect(store.create()).rejects.toThrow();
    });

    it('rejects a different private key paired with the saved public key', async () => {
        const first = await new IndexedDbIdentityStore(db).create();
        const other = await new IndexedDbIdentityStore(db, 'other').create();
        await expect(
            validateIdentity({ ...first, privateKey: other.privateKey }),
        ).rejects.toThrow();
    });
});

function setup() {
    const keys = new IndexedDbIdentityStore(db);
    let value: unknown;
    let failWrite = false;
    const local = {
        restrict: async () => undefined,
        read: async () => structuredClone(value),
        write: async (next: TrustSnapshot) => {
            if (failWrite) throw new Error('storage unavailable');
            value = structuredClone(next);
        },
        clear: async () => {
            value = undefined;
        },
    };
    let elapsed = 0;
    const live = new Set<string>();
    let chain = Promise.resolve();
    const lock = async <T>(fn: () => Promise<T>): Promise<T> => {
        const result = chain.then(fn);
        chain = result.then(
            () => undefined,
            () => undefined,
        );
        return result;
    };
    const verifier = async (_peer: unknown, _stage: string, proof: Uint8Array) => proof[0] === 1;
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
        );
    };
    return {
        keys,
        local,
        make,
        get: () => value as TrustSnapshot,
        corrupt: (next: unknown) => {
            value = next;
        },
        fail: () => {
            failWrite = true;
        },
        tick: (n: number) => {
            elapsed = n;
        },
        closeConnectors: () => live.clear(),
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

describe('Chrome trust repository', () => {
    it('does not orphan a new identity when the first operation is rejected', async () => {
        const s = setup();
        const repo = s.make();
        await expect(repo.forgetHint('00'.repeat(16))).rejects.toThrow();
        expect((await repo.identity()).installationId).toMatch(/^[0-9a-f]{32}$/);
        await expect(
            repo.addPending({ ...(await candidate()), label: 'x'.repeat(33) }),
        ).rejects.toThrow();
        expect(await repo.peers()).toEqual([]);
    });

    it('reports transient storage errors as retryable without requesting destructive re-pairing', async () => {
        const s = setup();
        const repo = s.make();
        await repo.identity();
        s.fail();
        await expect(repo.peers()).rejects.toThrow('unavailable');
        await expect(repo.peers()).rejects.not.toThrow('re-pair');
    });
    it('fails closed on unknown fields, unsupported versions, invalid timestamps and duplicate stored peers', async () => {
        const s = setup();
        const repo = s.make();
        await repo.addPending(await candidate());
        const original = structuredClone(s.get());
        const corruptions = [
            { ...original, version: 2 },
            { ...original, secret: 'not allowed' },
            { ...original, peers: [null] },
            { ...original, peers: [...original.peers, original.peers[0]] },
            { ...original, peers: [{ ...original.peers[0], createdAtMillis: -1 }] },
            { ...original, peers: [{ ...original.peers[0], deviceType: 1 }] },
            { ...original, peers: [{ ...original.peers[0], state: 'other' }] },
        ];
        for (const corruption of corruptions) {
            s.corrupt(corruption);
            await expect(repo.peers()).rejects.toThrow('re-pair');
        }
    });

    it('rejects malformed hints before publishing and cannot authenticate an unknown peer', async () => {
        const s = setup();
        const repo = s.make();
        const peer = await candidate();
        await repo.addPending(peer);
        await repo.confirm(peer.installationId, 'complete', new Uint8Array([1]));
        for (const hint of ['', 'é'.repeat(129), 'device\nsecret']) {
            await expect(
                repo.authenticated(peer.installationId, hint, new Uint8Array([1])),
            ).rejects.toThrow();
            expect(s.get().peers[0].platformHint).toBeUndefined();
        }
        await expect(
            repo.authenticated('00'.repeat(16), 'candidate', new Uint8Array([1])),
        ).rejects.toThrow();
    });

    it('treats monotonic clock rollback as loss of pending continuity', async () => {
        const s = setup();
        const repo = s.make();
        s.tick(100);
        await repo.addPending(await candidate());
        s.tick(99);
        expect(await repo.peers()).toEqual([]);
    });
    it('initializes once and binds metadata to the same surviving key', async () => {
        const s = setup();
        const identity = await s.make().identity();
        expect(await s.make().identity()).toEqual(identity);
        await s.local.clear();
        await expect(s.make().identity()).rejects.toThrow('re-pair');
    });

    it('requires authenticated transitions and preserves durable activation mirrors on context loss', async () => {
        const s = setup();
        const repo = s.make();
        const peer = await candidate();
        await repo.addPending(peer);
        expect(await repo.activePeers()).toEqual([]);
        await expect(
            repo.confirm(peer.installationId, 'complete', new Uint8Array([0])),
        ).rejects.toThrow();
        await repo.confirm(peer.installationId, 'complete', new Uint8Array([1]));
        expect((await s.make().peers())[0].state).toBe('active_activation_pending');
        await repo.confirm(peer.installationId, 'phone_active', new Uint8Array([1]));
        expect((await s.make().activePeers())[0].installationId).toBe(peer.installationId);
    });

    it('expires genuinely pending records at the monotonic deadline and on connector loss', async () => {
        const s = setup();
        const repo = s.make();
        await repo.addPending(await candidate());
        s.tick(86_399_999);
        expect(await repo.peers()).toHaveLength(1);
        s.tick(86_400_000);
        expect(await repo.peers()).toEqual([]);
        await repo.addPending(await candidate());
        s.closeConnectors();
        expect(await s.make().peers()).toEqual([]);
    });

    it('does not delete another still-live connector pending record during concurrent reads', async () => {
        const s = setup();
        const owner = s.make();
        const observer = s.make();
        const peer = await candidate();
        await owner.addPending(peer);
        expect(await observer.peers()).toHaveLength(1);
        await expect(
            observer.confirm(peer.installationId, 'complete', new Uint8Array([1])),
        ).rejects.toThrow();
        await owner.confirm(peer.installationId, 'complete', new Uint8Array([1]));
        expect((await observer.peers())[0].state).toBe('active_activation_pending');
    });

    it('rejects duplicate IDs, malformed keys, overlong UTF-8 labels and corrupt snapshots', async () => {
        const s = setup();
        const repo = s.make();
        const peer = await candidate();
        await repo.addPending(peer);
        await expect(repo.addPending(peer)).rejects.toThrow();
        await expect(
            repo.addPending({ ...(await candidate()), publicKey: '04' + '00'.repeat(64) }),
        ).rejects.toThrow();
        await expect(
            repo.addPending({ ...(await candidate()), label: 'é'.repeat(17) }),
        ).rejects.toThrow();
        const corrupt = structuredClone(s.get());
        corrupt.peers[0].fingerprint = '00'.repeat(32);
        s.corrupt(corrupt);
        await expect(repo.peers()).rejects.toThrow();
    });

    it('accepts hints only after authentication and deleting them never changes trust', async () => {
        const s = setup();
        const repo = s.make();
        const peer = await candidate();
        await repo.addPending(peer);
        await repo.confirm(peer.installationId, 'complete', new Uint8Array([1]));
        await expect(
            repo.authenticated(peer.installationId, 'winrt:secret', new Uint8Array([0])),
        ).rejects.toThrow();
        await repo.authenticated(peer.installationId, 'winrt:opaque', new Uint8Array([1]));
        expect((await repo.peers())[0].platformHint).toBe('winrt:opaque');
        await repo.forgetHint(peer.installationId);
        expect((await repo.peers())[0].platformHint).toBeUndefined();
        expect((await repo.peers())[0].publicKey).toBe(peer.publicKey);
    });

    it('serializes concurrent updates, isolates revocation and publishes nothing on write failure', async () => {
        const s = setup();
        const repo = s.make();
        const a = await candidate();
        const b = await candidate();
        await Promise.all([repo.addPending(a), repo.addPending(b)]);
        expect(await repo.peers()).toHaveLength(2);
        await repo.revoke(a.installationId);
        expect((await repo.peers()).map((p) => p.installationId)).toEqual([b.installationId]);
        s.fail();
        await expect(repo.revoke(b.installationId)).rejects.toThrow();
        expect(s.get().peers).toHaveLength(1);
    });

    it('requires explicit reset after key loss and never trusts old peers with a new identity', async () => {
        const s = setup();
        const repo = s.make();
        const first = await repo.identity();
        await repo.addPending(await candidate());
        await s.keys.clear();
        await expect(repo.peers()).rejects.toThrow('re-pair');
        await repo.reset();
        expect((await repo.identity()).installationId).not.toBe(first.installationId);
        expect(await repo.peers()).toEqual([]);
    });
});

describe('Chrome storage adapter', () => {
    it('restricts reads to trusted local contexts without touching sync storage', async () => {
        let access = '';
        let stored: Record<string, unknown> = {};
        vi.stubGlobal('chrome', {
            storage: {
                local: {
                    setAccessLevel: async ({ accessLevel }: { accessLevel: string }) => {
                        access = accessLevel;
                    },
                    get: async (key: string) => {
                        expect(access).toBe('TRUSTED_CONTEXTS');
                        return { [key]: stored[key] };
                    },
                    set: async (value: Record<string, unknown>) => {
                        stored = value;
                    },
                    remove: async (key: string) => {
                        Reflect.deleteProperty(stored, key);
                    },
                },
            },
        });
        try {
            const local = new ChromeLocalStore();
            await local.restrict();
            const snapshot: TrustSnapshot = {
                version: 1,
                identity: { installationId: 'id', publicKey: 'key' },
                peers: [],
            };
            await local.write(snapshot);
            expect(stored[TRUST_STORAGE_KEY]).toEqual(snapshot);
            expect(await local.read()).toEqual(snapshot);
            await local.clear();
            expect(await local.read()).toBeUndefined();
        } finally {
            vi.unstubAllGlobals();
        }
    });
});
