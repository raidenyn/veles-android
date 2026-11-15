// OTP-11: the Chrome revocation coordinator (design D1/D2/D3/D5/D6/D8).
//
// One coordinator per connector lifetime. It owns the order of every trust-changing operation
// and composes the trust repository with the session manager; it never talks to a transport
// directly and never selects a peer by a label, native handle or platform hint.
//
// The order is fixed per operation:
// - local `revoke`: fence, delete, terminate the matching sessions and bound enrollment links,
//   sweep, release the fence. Every later stage still runs in a `finally`, so a failed delete, a
//   failing transport close, a failed sweep or an abandoned caller never leaves the peer fenced
//   and never leaves a matching session alive.
// - handler-driven `deleteFenced` (mutual removal): fence, start the detached finish, delete.
//   The finish waits behind the handler's whole step (so the reply is delivered first) and only
//   its own unconditional `finally` releases the fence; nothing awaits the current generation
//   from inside the handler.
// - `failClosed` (store failure): terminate everything, non-destructively, and report the one
//   "re-pair required" status. It runs asynchronously, so a store failure observed inside a
//   session's handler step can never deadlock on that session's own step.
// - `reset`: terminate every enrollment link and every session first, then discard the stored
//   trust through the repository's own reset (its invalid marker included). Only an explicit
//   user action reaches this path.

import type { RemovalOperations } from '../session/removal';
import type { ChromeTrustRepository } from '../trust/repository';

import type { RemovalResult, SessionManager } from './sessions';

/**
 * One read-only peer listing entry (design D5). It is computed fresh from the repository plus the
 * session registry on every call and deliberately carries no key material, no platform hint and
 * no native handle: the fingerprint is the only identity digest a surface may show.
 */
export interface PeerSummary {
    installationId: string;
    label: string;
    fingerprint: string;
    deviceType: number;
    state: string;
    createdAtMillis: number;
    lastSeenMillis: number | null;
    connected: boolean;
}

/**
 * The only trust status a surface may show (design D6). `re-pair-required` is the single
 * fail-closed message, and it never suggests erasing trust automatically.
 */
export type TrustStatus = 'ok' | 're-pair-required';

export interface RevocationCoordinator {
    /** The peers as the repository holds them right now, with a live-session `connected` flag. */
    peers(): Promise<PeerSummary[]>;
    /** Local revocation: delete first, always terminate (design D2/D8). */
    revoke(peerId: string): Promise<void>;
    /** Ask the peer to remove this endpoint's record (OTP2-REC-013); never deletes locally. */
    removeMutually(peerId: string): Promise<RemovalResult>;
    /** The fail-closed recovery of design D6: terminate everything, delete nothing. */
    failClosed(): void;
    /** The explicit identity reset: terminate everything, then discard the trust and the key. */
    reset(): Promise<void>;
}

export interface RevocationCoordinatorDeps {
    repo: ChromeTrustRepository;
    sessions: SessionManager;
    /** The status sink; the fail-closed action reports the one "re-pair required" value. */
    onStatus?: (status: TrustStatus) => void;
    /** The OTP-history cleanup extension point; it has no consumer until OTP-13. */
    historyCleanup?: (peerId: string) => void;
}

export function createRevocationCoordinator(
    deps: RevocationCoordinatorDeps,
): RevocationCoordinator & RemovalOperations {
    return new ChromeRevocationCoordinator(
        deps.repo,
        deps.sessions,
        deps.onStatus ?? (() => undefined),
        deps.historyCleanup ?? (() => undefined),
    );
}

class ChromeRevocationCoordinator implements RevocationCoordinator, RemovalOperations {
    constructor(
        private readonly repo: ChromeTrustRepository,
        private readonly sessions: SessionManager,
        private readonly onStatus: (status: TrustStatus) => void,
        private readonly historyCleanup: (peerId: string) => void,
    ) {}

    async peers(): Promise<PeerSummary[]> {
        const connected = new Set(
            this.sessions.authenticatedSessions().map((session) => session.peerId),
        );
        return (await this.repo.peers()).map((peer) => ({
            installationId: peer.installationId,
            label: peer.label,
            fingerprint: peer.fingerprint,
            deviceType: peer.deviceType,
            state: peer.state,
            createdAtMillis: peer.createdAtMillis,
            lastSeenMillis: peer.lastSeenMillis,
            connected: connected.has(peer.installationId),
        }));
    }

    /**
     * Revokes one peer locally (design D2/D8). The fence is taken first and released last; the
     * delete, the termination, the sweep and the release are nested `finally` blocks, so nothing
     * can skip a later stage.
     *
     * A failed delete is a store failure: the repository reports it at its choke point and the
     * composition root routes that to `failClosed`. The failure is still rethrown to the caller
     * after the matching connections have been terminated and the fence released.
     */
    async revoke(peerId: string): Promise<void> {
        this.repo.beginRevocation(peerId);
        try {
            await this.repo.revoke(peerId);
            this.cleanupHistory(peerId);
        } finally {
            try {
                await this.terminate(peerId);
            } finally {
                try {
                    await this.sweep(peerId);
                } finally {
                    this.repo.endRevocation(peerId);
                }
            }
        }
    }

    /**
     * Asks the peer to remove this endpoint's record (OTP2-REC-013). The local record is deleted
     * only by the validated response's handler, so the user keeps local `revoke` as a separate
     * choice when the response is lost.
     */
    removeMutually(peerId: string): Promise<RemovalResult> {
        return this.sessions.requestRemoval(peerId);
    }

    /**
     * The fail-closed action of design D6: terminate every session and every enrollment link,
     * delete nothing, and report the one "re-pair required" status.
     *
     * The termination is deferred by one microtask instead of running inline because the callback
     * that reaches this method can run inside a generation's handler step; awaiting that
     * generation from inside its own step would deadlock on the step it holds.
     */
    failClosed(): void {
        this.onStatus('re-pair-required');
        void Promise.resolve()
            .then(() => this.sessions.closeAll())
            .catch(() => undefined);
    }

    /**
     * The explicit identity reset (design D6): every enrollment link and every session ends
     * before anything is deleted, then the repository's own reset — invalid marker first — discards
     * the stored trust and the identity. It is idempotent and is never reached from a failure path.
     */
    async reset(): Promise<void> {
        try {
            await this.sessions.closeAll();
        } catch {
            // The generations were logically torn down; the deletion must still run.
        }
        // The repository owns the reset body (invalid marker, key removal, snapshot clear); the
        // coordinator never duplicates it.
        await this.repo.reset();
    }

    /**
     * The handler-driven removal (design D2/D4/D8): take the fence, start the detached finish and
     * delete inside the handler's step. The finish begins immediately — even when the caller's own
     * task is abandoned — and its own unconditional `finally` is the only place the fence is
     * released, so the fence can be neither released early nor released twice.
     */
    async deleteFenced(peerId: string): Promise<void> {
        this.repo.beginRevocation(peerId);
        // Started (not awaited): the finish waits behind the handler's step, so awaiting it from
        // inside that step would deadlock.
        void this.finish(peerId).catch(() => undefined);
        await this.repo.revoke(peerId);
        this.cleanupHistory(peerId);
    }

    /**
     * The detached completion of a handler-driven removal: terminate every connection bound to
     * the peer (the current one is already ending through the engine's terminal path, so this only
     * waits behind its step), run the sweep that removes anything written between the fence and
     * the termination, and release the fence.
     */
    private async finish(peerId: string): Promise<void> {
        try {
            await this.terminate(peerId);
        } finally {
            try {
                await this.sweep(peerId);
            } finally {
                this.repo.endRevocation(peerId);
            }
        }
    }

    /**
     * The final sweep of design D8: delete the id again so an `addPending` that passed the fence
     * before it existed cannot outlive the revocation. The repository reports a store failure at
     * its choke point and rethrows it; that callback is the escalation, so the sweep itself does
     * not report twice and never aborts the fence release.
     */
    private async sweep(peerId: string): Promise<void> {
        try {
            await this.repo.revoke(peerId);
        } catch {
            // Reported by the repository's store-failure episode; the fence release still runs.
        }
    }

    /** Terminate every matching connection; a failing transport close is best-effort. */
    private async terminate(peerId: string): Promise<void> {
        try {
            await this.sessions.closePeer(peerId);
        } catch {
            // Logical termination is authoritative: the engines erased their keys and stopped
            // record processing before the transport close was attempted.
        }
    }

    /**
     * The OTP-history extension point: it has no consumer yet and never fails a removal. A
     * consumer runs after the removal's already-committed delete, so everything it throws is
     * isolated here.
     */
    private cleanupHistory(peerId: string): void {
        try {
            this.historyCleanup(peerId);
        } catch {
            // OTP-13 registers the first consumer; nothing it throws may fail a removal whose
            // record is already deleted.
        }
    }
}
