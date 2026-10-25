import { IndexedDbIdentityStore, type IdentityStore } from './identity';
import { browserPendingOwner, type PendingOwner } from './continuity';
import { ChromeLocalStore, trustLock, type ExclusiveLock, type LocalStore } from './storage';
import {
    bytes,
    exactFields,
    fingerprint,
    importPublicKey,
    requireTrust,
    TrustError,
    TrustUnavailableError,
    type InstallationIdentity,
    type PendingPeer,
    type ProofStage,
    type TrustedPeer,
    type TrustSnapshot,
} from './model';

export { TrustError, TrustUnavailableError } from './model';

/** Must verify the stage-specific MAC/signature, ET and current authenticated session.
 * The callback is supplied by protocol code, never a UI flag or persisted evidence.
 */
export type AuthenticationVerifier = (
    peer: Readonly<TrustedPeer>,
    stage: ProofStage,
    proof: Uint8Array,
) => Promise<boolean>;

/** One instance per connector lifetime. A new connector cannot inherit a pending clock. */
export class ChromeTrustRepository {
    constructor(
        private readonly keys: IdentityStore = new IndexedDbIdentityStore(),
        private readonly local: LocalStore = new ChromeLocalStore(),
        private readonly lock: ExclusiveLock = trustLock,
        private readonly verify: AuthenticationVerifier = async () => false,
        private readonly elapsed: () => number = () => performance.now(),
        private readonly wall: () => number = () => Date.now(),
        private readonly owner: PendingOwner = browserPendingOwner(),
    ) {}

    identity(): Promise<InstallationIdentity> {
        return this.update((s) => structuredClone(s.identity));
    }
    peers(): Promise<TrustedPeer[]> {
        return this.update((s) => structuredClone(s.peers));
    }
    async activePeers(): Promise<TrustedPeer[]> {
        return (await this.peers()).filter((p) => p.state === 'active');
    }

    addPending(input: PendingPeer): Promise<void> {
        const candidate = structuredClone(input);
        return this.update(async (s) => {
            exactFields(candidate, [
                'installationId',
                'publicKey',
                'label',
                'deviceType',
                'enrollmentTranscript',
            ]);
            requireTrust(!s.peers.some((p) => p.installationId === candidate.installationId));
            const peer: TrustedPeer = {
                ...candidate,
                fingerprint: await fingerprint(candidate.publicKey, s.identity.publicKey),
                state: 'pending',
                createdAtMillis: this.wall(),
                lastSeenMillis: null,
                pendingContext: this.owner.id,
                pendingSince: this.elapsed(),
            };
            await this.validatePeer(peer, s.identity);
            s.peers.push(peer);
        });
    }

    confirm(peerId: string, stage: 'complete' | 'phone_active', proof: Uint8Array): Promise<void> {
        const evidence = proof.slice();
        return this.update(async (s) => {
            const peer = this.find(s, peerId);
            requireTrust(peer.state !== 'pending' || peer.pendingContext === this.owner.id);
            requireTrust(stage === 'complete' || stage === 'phone_active');
            requireTrust(stage === 'complete' ? peer.state !== 'active' : peer.state !== 'pending');
            requireTrust(await this.verify(structuredClone(peer), stage, evidence));
            peer.state = stage === 'complete' ? 'active_activation_pending' : 'active';
            // Active-family records never depend on connector continuity or a delete deadline.
            peer.pendingContext = null;
            peer.pendingSince = null;
        });
    }

    authenticated(peerId: string, hint: string | undefined, proof: Uint8Array): Promise<void> {
        const evidence = proof.slice();
        return this.update(async (s) => {
            const peer = this.find(s, peerId);
            requireTrust(peer.state !== 'pending');
            requireTrust(await this.verify(structuredClone(peer), 'authenticated', evidence));
            if (hint !== undefined) {
                this.validateHint(hint);
                peer.platformHint = hint;
            }
            peer.lastSeenMillis = this.wall();
        });
    }

    forgetHint(peerId: string): Promise<void> {
        return this.update((s) => {
            delete this.find(s, peerId).platformHint;
        });
    }
    revoke(peerId: string): Promise<void> {
        return this.update((s) => {
            s.peers = s.peers.filter((p) => p.installationId !== peerId);
        });
    }

    /** Explicit user recovery discards all trust; never invoked automatically on errors. */
    async reset(): Promise<void> {
        try {
            await this.lock(async () => {
                await this.local.restrict();
                requireTrust(await this.owner.isAlive(this.owner.id));
                // Durable invalid marker makes any interrupted reset fail closed.
                await this.local.write({
                    version: 1,
                    identity: { installationId: '', publicKey: '' },
                    peers: [],
                });
                await this.keys.clear();
                await this.local.clear();
            });
        } catch (error) {
            throw error instanceof TrustError ? error : new TrustUnavailableError();
        }
    }

    private find(s: TrustSnapshot, id: string): TrustedPeer {
        const peer = s.peers.find((p) => p.installationId === id);
        requireTrust(peer);
        return peer;
    }

    private async update<T>(operation: (snapshot: TrustSnapshot) => T | Promise<T>): Promise<T> {
        try {
            return await this.lock(async () => {
                await this.local.restrict();
                requireTrust(await this.owner.isAlive(this.owner.id));
                let identity = await this.keys.read();
                const raw = await this.local.read();
                let snapshot: TrustSnapshot;
                if (raw === undefined) {
                    requireTrust(identity === undefined);
                    identity = await this.keys.create();
                    snapshot = {
                        version: 1,
                        identity: {
                            installationId: identity.installationId,
                            publicKey: identity.publicKey,
                        },
                        peers: [],
                    };
                    // A rejected first operation must not strand a valid key
                    // without metadata. Only an actual interrupted save fails closed.
                    await this.local.write(snapshot);
                } else {
                    requireTrust(identity);
                    exactFields(raw, ['version', 'identity', 'peers']);
                    requireTrust(raw.version === 1);
                    exactFields(raw.identity, ['installationId', 'publicKey']);
                    requireTrust(
                        raw.identity.installationId === identity.installationId &&
                            raw.identity.publicKey === identity.publicKey,
                    );
                    requireTrust(Array.isArray(raw.peers) && raw.peers.length <= 256);
                    snapshot = raw as unknown as TrustSnapshot;
                    for (const peer of snapshot.peers)
                        await this.validatePeer(peer, snapshot.identity);
                    requireTrust(
                        new Set(snapshot.peers.map((p) => p.installationId)).size ===
                            snapshot.peers.length,
                    );
                }
                const now = this.elapsed();
                requireTrust(Number.isFinite(now) && now >= 0);
                const retained: TrustedPeer[] = [];
                for (const peer of snapshot.peers) {
                    if (peer.state !== 'pending') {
                        retained.push(peer);
                        continue;
                    }
                    if (peer.pendingContext === this.owner.id) {
                        if (
                            peer.pendingSince !== null &&
                            now >= peer.pendingSince &&
                            now - peer.pendingSince < 86_400_000
                        )
                            retained.push(peer);
                    } else if (
                        peer.pendingContext !== null &&
                        (await this.owner.isAlive(peer.pendingContext))
                    ) {
                        // Another live connector owns this monotonic interval. Do not
                        // recreate its clock or allow this instance to activate its pending record.
                        retained.push(peer);
                    }
                }
                snapshot.peers = retained;
                const result = await operation(snapshot);
                requireTrust(snapshot.peers.length <= 256);
                for (const peer of snapshot.peers) await this.validatePeer(peer, snapshot.identity);
                await this.local.write(snapshot);
                return result;
            });
        } catch (error) {
            throw error instanceof TrustError ? error : new TrustUnavailableError();
        }
    }

    private validateHint(hint: unknown): asserts hint is string {
        requireTrust(
            typeof hint === 'string' &&
                hint.length > 0 &&
                new TextEncoder().encode(hint).length <= 256 &&
                Array.from(hint).every(
                    (char) => char.charCodeAt(0) >= 32 && char.charCodeAt(0) !== 127,
                ),
        );
    }

    private async validatePeer(value: unknown, identity: InstallationIdentity): Promise<void> {
        exactFields(
            value,
            [
                'installationId',
                'publicKey',
                'label',
                'deviceType',
                'enrollmentTranscript',
                'fingerprint',
                'state',
                'createdAtMillis',
                'lastSeenMillis',
                'pendingContext',
                'pendingSince',
            ],
            ['platformHint'],
        );
        const peer = value as unknown as TrustedPeer;
        bytes(peer.installationId, 16);
        bytes(peer.enrollmentTranscript, 32);
        requireTrust(peer.installationId !== identity.installationId && peer.deviceType === 0);
        await importPublicKey(peer.publicKey);
        requireTrust(
            typeof peer.label === 'string' && new TextEncoder().encode(peer.label).length <= 32,
        );
        requireTrust(peer.fingerprint === (await fingerprint(peer.publicKey, identity.publicKey)));
        requireTrust(['pending', 'active_activation_pending', 'active'].includes(peer.state));
        requireTrust(Number.isSafeInteger(peer.createdAtMillis) && peer.createdAtMillis >= 0);
        requireTrust(
            peer.lastSeenMillis === null ||
                (Number.isSafeInteger(peer.lastSeenMillis) && peer.lastSeenMillis >= 0),
        );
        if (peer.state === 'pending') {
            requireTrust(
                typeof peer.pendingContext === 'string' &&
                    /^[0-9a-f-]{36}$/.test(peer.pendingContext),
            );
            requireTrust(
                typeof peer.pendingSince === 'number' &&
                    Number.isFinite(peer.pendingSince) &&
                    peer.pendingSince >= 0,
            );
            requireTrust(peer.lastSeenMillis === null && peer.platformHint === undefined);
        } else {
            requireTrust(peer.pendingContext === null && peer.pendingSince === null);
        }
        if (peer.platformHint !== undefined) this.validateHint(peer.platformHint);
    }
}
