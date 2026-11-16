import type { ActivationTrustRepository } from '../../src/pairing/activation';
import type { ClientTrustRepository } from '../../src/pairing/client-machine';
import { normalizeLowS } from '../../src/session/ecdsa';
import type {
    ApplicationHandler,
    ClientEngineDeps,
    PhoneRecordState,
} from '../../src/session/engine';
import {
    bytes,
    fingerprint,
    hex,
    TrustError,
    type InstallationIdentity,
    type PendingPeer,
    type TrustedPeer,
} from '../../src/trust/model';

// OTP-12 design D8: the scripted client owns an in-memory trust repository. It
// satisfies the enrollment machine, the recovery activation engine and the session
// engine lookups, and it never imports `src/trust/repository.ts` or
// `src/trust/storage.ts` (which reach the browser extension storage API).
//
// Records are public metadata only: hex installation ids and public keys plus the
// state transitions. The client's signing key lives beside the identity, never in a
// record.

export interface DerivedIdentity {
    installationId: Uint8Array;
    point: Uint8Array;
    key: CryptoKey;
}

/** No public `ET`: a reconnect that needs no recovery keys. */
const NO_ENROLLMENT_TRANSCRIPT = '';

/** A fixed public-metadata timestamp for the in-memory records. */
const CREATED_MILLIS = 1_000;

function clonePeer(peer: TrustedPeer): TrustedPeer {
    return { ...peer };
}

export class MemoryTrust implements ClientTrustRepository, ActivationTrustRepository {
    readonly #local: DerivedIdentity;
    readonly #localPublicKey: string;
    readonly #localInstallationId: string;
    readonly #peers: TrustedPeer[] = [];

    constructor(local: DerivedIdentity) {
        this.#local = local;
        this.#localPublicKey = hex(local.point);
        this.#localInstallationId = hex(local.installationId);
    }

    static async withActivePeer(
        local: DerivedIdentity,
        peer: DerivedIdentity,
    ): Promise<MemoryTrust> {
        const trust = new MemoryTrust(local);
        trust.#peers.push({
            installationId: hex(peer.installationId),
            publicKey: hex(peer.point),
            label: 'Veles loopback phone',
            deviceType: 0x00,
            enrollmentTranscript: NO_ENROLLMENT_TRANSCRIPT,
            fingerprint: await fingerprint(hex(peer.point), trust.#localPublicKey),
            state: 'active',
            createdAtMillis: CREATED_MILLIS,
            lastSeenMillis: null,
            pendingContext: null,
            pendingSince: null,
        });
        return trust;
    }

    localInstallationId(): Uint8Array {
        return this.#local.installationId.slice();
    }

    localPoint(): Uint8Array {
        return this.#local.point.slice();
    }

    async identity(): Promise<InstallationIdentity> {
        return { installationId: this.#localInstallationId, publicKey: this.#localPublicKey };
    }

    async peers(): Promise<TrustedPeer[]> {
        return this.#peers.map(clonePeer);
    }

    async addPending(peer: PendingPeer): Promise<void> {
        if (this.#peers.some((existing) => existing.installationId === peer.installationId)) {
            throw new TrustError();
        }
        this.#peers.push({
            ...peer,
            fingerprint: await fingerprint(peer.publicKey, this.#localPublicKey),
            state: 'pending',
            createdAtMillis: CREATED_MILLIS,
            lastSeenMillis: null,
            pendingContext: null,
            pendingSince: null,
        });
    }

    async confirm(
        peerId: string,
        stage: 'complete' | 'phone_active',
        proof: Uint8Array,
    ): Promise<void> {
        // The machine and the activation engine call this only after verifying the
        // proof; the in-memory store records the resulting public state.
        void proof;
        const peer = this.#require(peerId);
        if (stage === 'complete') {
            peer.state = 'active_activation_pending';
            return;
        }
        peer.state = 'active';
    }

    async revoke(peerId: string): Promise<void> {
        const index = this.#peers.findIndex((peer) => peer.installationId === peerId);
        if (index >= 0) this.#peers.splice(index, 1);
    }

    async revokePending(peerId: string): Promise<boolean> {
        const index = this.#peers.findIndex((peer) => peer.installationId === peerId);
        if (index < 0) return false;
        if (this.#peers[index]?.state !== 'pending') return false;
        this.#peers.splice(index, 1);
        return true;
    }

    async lookup(
        peerId: string,
    ): Promise<{ publicKey: Uint8Array; state: PhoneRecordState; et?: Uint8Array } | undefined> {
        const peer = this.#peers.find((candidate) => candidate.installationId === peerId);
        if (peer === undefined) return undefined;
        return {
            publicKey: bytes(peer.publicKey, 65),
            state: peer.state,
            ...(peer.enrollmentTranscript === NO_ENROLLMENT_TRANSCRIPT
                ? {}
                : { et: bytes(peer.enrollmentTranscript, 32) }),
        };
    }

    async sign(message: Uint8Array): Promise<Uint8Array> {
        return normalizeLowS(
            new Uint8Array(
                await crypto.subtle.sign(
                    { name: 'ECDSA', hash: 'SHA-256' },
                    this.#local.key,
                    message,
                ),
            ),
        );
    }

    engineDeps(
        peerId: string,
        options: { handler?: ApplicationHandler; now?: () => number } = {},
    ): Omit<ClientEngineDeps, 'ephemeral' | 'nonce'> {
        return {
            localInstallationId: this.#local.installationId.slice(),
            sign: (message) => this.sign(message),
            expectedPeerId: peerId,
            isCurrent: () => true,
            lookupPhone: (id) => this.lookup(id),
            upgradeMirror: (id, proof) => this.confirm(id, 'phone_active', proof),
            now: options.now ?? (() => 1_000),
            ...(options.handler === undefined ? {} : { handler: options.handler }),
        };
    }

    #require(peerId: string): TrustedPeer {
        const peer = this.#peers.find((candidate) => candidate.installationId === peerId);
        if (peer === undefined) throw new TrustError();
        return peer;
    }
}
