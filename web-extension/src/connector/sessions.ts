// Connector-side session owner: one client engine per phone link.
//
// The connector no longer treats a transport `connected` event as success. A
// successful `connect` hands the connection handle and one intended trust
// record id to this manager, which:
//   - runs the §11.3 client-role engine for that handle,
//   - serializes every inbound record and outbound request through one bounded
//     queue (§11.3, OTP2-REC-034 queue bound),
//   - drops any completion whose handle or port generation changed,
//   - answers the trust repository's stage-bound verifier itself, using the
//     engine's immutable verified phone confirm,
//   - records `lastSeen`/the platform hint only after the gate opened for the
//     current generation, fenced through the repository's final write, and
//   - drives a cancellable coarse deadline tick for silent links.
//
// Discovery metadata never supplies the peer id: recovery passes the target's
// id and manual Connect passes an explicit id or the single active-family
// trusted phone (`intendedPeer`).

import type { TrustedPeer } from '../trust/model';
import type { AuthenticationVerifier, ChromeTrustRepository } from '../trust/repository';
import {
    ClientSessionEngine,
    PEER_STATE_ACTIVE,
    type ClientEngineDeps,
    type SessionEffect,
} from '../session/engine';
import { QueueOverflow, SESSION_QUEUE_LIMIT, SerialQueue } from '../session/queue';
import { SessionReject } from '../session/reject';
import type { SessionClass } from '../session/wire';

import type { DiagnosticRing } from './diagnostics';
import type { ConnectorClose, ConnectorResult } from './port';
import type { WireEvent } from './protocol';
import type { ConnectorSnapshot } from './state';

export type SentResult = ConnectorResult<Extract<WireEvent, { type: 'sent' }>>;

export interface SessionRecord {
    connectionHandle: string;
    bytes: Uint8Array;
}

/** The controller surface a session needs. `ConnectorController` satisfies it. */
export interface SessionController {
    send(connectionHandle: string, record: Uint8Array): SentResult | Promise<SentResult>;
    disconnect(
        connectionHandle: string,
    ): Promise<ConnectorResult<Extract<WireEvent, { type: 'disconnected' }>>>;
    onRecord(listener: (record: SessionRecord) => void): () => void;
    onChange(listener: (snapshot: ConnectorSnapshot) => void): () => void;
    onClose(listener: (close: ConnectorClose) => void): () => void;
    isOpen(): boolean;
    snapshot(): ConnectorSnapshot;
    readonly portGeneration: number;
    readonly state: { readonly diagnostics: DiagnosticRing };
}

export type SessionOpenResult = { result: 'authenticated'; peerId: string } | { result: 'failed' };

/** One authenticated session with its fixed class and copied 8-byte session id prefix. */
export interface AuthenticatedSession {
    readonly handle: string;
    readonly peerId: string;
    readonly sessionClass: SessionClass;
    readonly sessionIdPrefix: Uint8Array;
}

type SessionPhase = 'handshake' | 'open' | 'closed';

interface Session {
    readonly handle: string;
    readonly peerId: string;
    /** The port generation this session was opened on. */
    readonly generation: number;
    readonly engine: ClientSessionEngine;
    readonly queue: SerialQueue;
    phase: SessionPhase;
    settled: boolean;
    /** The pending coarse deadline tick, cancelled on every teardown path. */
    tickTimer: unknown;
    resolve(result: SessionOpenResult): void;
}

/**
 * Coarse deadline driver from the plan: deadlines are additionally evaluated on
 * a 15 s `setTimeout` that may be throttled. A late tick can only close a
 * session late because every expiry check compares injected monotonic `now()`;
 * throttling can never expire a session early.
 */
export const SESSION_TICK_MS = 15_000;

export interface SessionTimers {
    setTimeout(fn: () => void, ms: number): unknown;
    clearTimeout(handle: unknown): void;
}

const GLOBAL_TIMERS: SessionTimers = {
    setTimeout: (fn, ms) => globalThis.setTimeout(fn, ms),
    clearTimeout: (handle) => globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>),
};

function equalBytes(left: Uint8Array, right: Uint8Array): boolean {
    if (left.length !== right.length) return false;
    let difference = 0;
    for (let index = 0; index < left.length; index += 1) {
        difference |= left[index] ^ right[index];
    }
    return difference === 0;
}

function activeFamily(peer: TrustedPeer): boolean {
    return peer.state === 'active' || peer.state === 'active_activation_pending';
}

export class SessionManager {
    readonly repository: ChromeTrustRepository;
    readonly verifier: AuthenticationVerifier;

    private readonly byHandle = new Map<string, Session>();
    private readonly timers: SessionTimers;

    constructor(
        private readonly controller: SessionController,
        repository: ChromeTrustRepository,
        private readonly deps: (handle: string, peerId: string) => ClientEngineDeps,
        private readonly portGeneration: () => number,
        timers: Partial<SessionTimers> = {},
    ) {
        this.repository = repository;
        this.timers = {
            setTimeout: timers.setTimeout ?? GLOBAL_TIMERS.setTimeout,
            clearTimeout: timers.clearTimeout ?? GLOBAL_TIMERS.clearTimeout,
        };
        this.verifier = async (peer, stage, proof) => {
            if (stage !== 'phone_active' && stage !== 'authenticated') return false;
            for (const session of this.byHandle.values()) {
                if (session.peerId !== peer.installationId) continue;
                if (!this.isCurrent(session, session.generation)) continue;
                // The proof is the engine's immutable, fully verified phone
                // confirm; raw inbound bytes are never used as evidence.
                const confirm = session.engine.verifiedPhoneConfirm;
                if (confirm === null || !equalBytes(confirm, proof)) continue;
                if (
                    stage === 'phone_active' &&
                    session.phase === 'handshake' &&
                    session.engine.phoneConfirmFinalizing &&
                    // OTP2-SID-001: only a proof that the phone reported itself active
                    // (`peer_state = 0x01`) may upgrade a durable mirror; a below-active
                    // confirm still opens the session but proves nothing to the mirror.
                    session.engine.verifiedPeerState === PEER_STATE_ACTIVE
                ) {
                    return true;
                }
                if (stage === 'authenticated' && session.phase === 'open') return true;
            }
            return false;
        };
        this.controller.onRecord((record) => this.handleRecord(record));
        this.controller.onChange((snapshot) => this.prune(snapshot));
        this.controller.onClose(() => this.abortAll());
    }

    /**
     * `explicit` wins; otherwise exactly one active-family trusted record (by
     * installation id) is chosen. Zero or several candidates are unresolved:
     * the caller fails closed before any transport connect.
     */
    static intendedPeer(trusted: readonly TrustedPeer[], explicit?: string): string | undefined {
        if (explicit !== undefined && explicit.length > 0) return explicit;
        const ids = new Set<string>();
        for (const peer of trusted) {
            if (activeFamily(peer)) ids.add(peer.installationId);
        }
        if (ids.size !== 1) return undefined;
        return ids.values().next().value;
    }

    /**
     * Starts one session for `handle` and resolves only when the authentication
     * gate opened and the class is fixed (metadata persisted), or on failure.
     */
    open(handle: string, peerId: string): Promise<SessionOpenResult> {
        if (this.byHandle.has(handle)) return Promise.resolve({ result: 'failed' });
        const generation = this.portGeneration();
        let base: ClientEngineDeps;
        try {
            base = this.deps(handle, peerId);
        } catch {
            return Promise.resolve({ result: 'failed' });
        }
        let settle!: (result: SessionOpenResult) => void;
        const result = new Promise<SessionOpenResult>((resolve) => {
            settle = resolve;
        });
        let session: Session | null = null;
        const engine = new ClientSessionEngine({
            ...base,
            expectedPeerId: peerId,
            isCurrent: () => session !== null && this.isCurrent(session, generation),
        });
        session = {
            handle,
            peerId,
            generation,
            engine,
            queue: new SerialQueue(SESSION_QUEUE_LIMIT),
            phase: 'handshake',
            settled: false,
            tickTimer: undefined,
            resolve: settle,
        };
        this.byHandle.set(handle, session);
        this.armTick(session);
        this.enqueue(session, () => engine.start());
        return result;
    }

    /** Seals and sends one application record inside this session's queue. */
    sendApplication(handle: string, type: number, body: Uint8Array): Promise<void> {
        const session = this.byHandle.get(handle);
        if (session === undefined || session.phase !== 'open') {
            return Promise.reject(new SessionReject());
        }
        const copy = body.slice();
        return this.enqueue(session, () => session.engine.sendApplication(type, copy)).then(
            () => undefined,
        );
    }

    /**
     * Sessions whose authentication gate opened (the class is fixed). Handshaking, failed and
     * closed sessions are never listed. Exposed for the development-only probe's narrow
     * `full`-session selection.
     */
    authenticatedSessions(): AuthenticatedSession[] {
        const result: AuthenticatedSession[] = [];
        for (const session of this.byHandle.values()) {
            if (session.phase !== 'open') continue;
            const sessionClass = session.engine.sessionClass;
            const prefix = session.engine.sessionIdPrefix;
            if (sessionClass === null || prefix === null || prefix.length !== 8) continue;
            result.push({
                handle: session.handle,
                peerId: session.peerId,
                sessionClass,
                sessionIdPrefix: prefix,
            });
        }
        return result;
    }

    /** Ends one phone's session and, when its link is still live, only that link. */
    close(handle: string): void {
        const session = this.byHandle.get(handle);
        if (session !== undefined) this.terminate(session, 'closed', true);
    }

    private enqueue(session: Session, job: () => Promise<SessionEffect[]>): Promise<void> {
        const generation = this.portGeneration();
        const task = session.queue.run(async () => {
            const effects = await job();
            if (!this.isCurrent(session, generation)) return;
            await this.apply(session, generation, effects);
        });
        void task.catch((error: unknown) => {
            if (error instanceof QueueOverflow) {
                this.terminate(session, 'resource_exhausted', true);
                return;
            }
            if (error instanceof SessionReject) return;
            this.terminate(session, 'dependency', true);
        });
        return task;
    }

    private async apply(
        session: Session,
        generation: number,
        effects: SessionEffect[],
    ): Promise<void> {
        for (const effect of effects) {
            if (!this.isCurrent(session, generation)) return;
            switch (effect.kind) {
                case 'send': {
                    const sent = await this.controller.send(session.handle, effect.bytes);
                    if (!this.isCurrent(session, generation)) return;
                    if (effect.completionToken !== undefined) {
                        // Completion belongs to this serialized step: applying it
                        // inline opens the gate before any buffered inbound record
                        // (e.g. the phone confirm and its first application record)
                        // is dequeued, so nothing is rejected merely because the
                        // local confirm acknowledgement was still pending.
                        const token = effect.completionToken;
                        const completion = await session.engine.onSendComplete(token, sent.ok);
                        if (!this.isCurrent(session, generation)) return;
                        await this.apply(session, generation, completion);
                    } else if (!sent.ok) {
                        this.terminate(session, 'send_failed', true);
                        return;
                    }
                    break;
                }
                case 'authenticated': {
                    const applications = effect.unprompted ?? [];
                    try {
                        await this.authenticated(session, effect.peerId);
                        if (!this.isCurrent(session, generation)) return;
                        // OTP2-STA-010b steps 0/5: the owner sends what the session owes only
                        // after it handled the gate, never from inside the handler's context hook.
                        for (const application of applications) {
                            const sent = await session.engine.sendApplication(
                                application.type,
                                application.body,
                            );
                            await this.apply(session, generation, sent);
                            if (
                                session.phase === 'closed' ||
                                !this.isCurrent(session, generation)
                            ) {
                                return;
                            }
                        }
                    } finally {
                        for (const application of applications) application.body.fill(0);
                    }
                    return;
                }
                case 'terminated': {
                    this.terminate(session, effect.reason, true);
                    return;
                }
                case 'close': {
                    // The engine's only bare close is the generic pre-auth
                    // rejection, which must also close this phone's link.
                    this.terminate(session, 'closed_wire', true);
                    return;
                }
                case 'deliver':
                    break;
            }
        }
    }

    private async authenticated(session: Session, peerId: string): Promise<void> {
        if (session.phase !== 'handshake') return;
        // The class is fixed at this point; the verifier's `authenticated`
        // stage is only valid for an open session of the current generation.
        session.phase = 'open';
        if (!this.isCurrent(session, session.generation)) {
            this.terminate(session, 'authentication', true);
            return;
        }
        const proof = session.engine.verifiedPhoneConfirm;
        if (proof === null) {
            this.terminate(session, 'authentication', true);
            return;
        }
        const hint = this.connectionHint(session.handle);
        try {
            // The repository re-checks this synchronous fence immediately before
            // its durable write, after its own post-verifier validation.
            await this.repository.authenticated(peerId, hint, proof, () =>
                this.isCurrent(session, session.generation),
            );
        } catch {
            this.terminate(session, 'authentication', true);
            return;
        }
        this.settle(session, { result: 'authenticated', peerId });
    }

    private connectionHint(handle: string): string | undefined {
        return this.controller
            .snapshot()
            .connections.find((connection) => connection.handle === handle)?.platformHint;
    }

    private isCurrent(session: Session, generation: number): boolean {
        if (session.phase === 'closed') return false;
        if (this.byHandle.get(session.handle) !== session) return false;
        if (this.portGeneration() !== generation) return false;
        const connection = this.controller
            .snapshot()
            .connections.find((candidate) => candidate.handle === session.handle);
        return connection !== undefined && connection.status === 'connected';
    }

    private settle(session: Session, result: SessionOpenResult): void {
        if (session.settled) return;
        session.settled = true;
        session.resolve(result);
    }

    private terminate(session: Session, reason: string, closeLink: boolean): void {
        if (session.phase === 'closed') return;
        session.phase = 'closed';
        this.cancelTick(session);
        session.engine.teardown();
        if (this.byHandle.get(session.handle) === session) this.byHandle.delete(session.handle);
        this.controller.state.diagnostics.push({
            direction: 'local',
            type: 'session_terminated',
            code: reason,
            connection_handle: session.handle,
        });
        this.settle(session, { result: 'failed' });
        if (closeLink && this.controller.isOpen()) {
            void this.controller.disconnect(session.handle).catch(() => undefined);
        }
    }

    private handleRecord(record: SessionRecord): void {
        const session = this.byHandle.get(record.connectionHandle);
        if (session === undefined || session.phase === 'closed') return;
        const bytes = record.bytes.slice();
        this.enqueue(session, () => session.engine.onInbound(bytes));
    }

    /**
     * Arms the next coarse deadline check. It runs outside the queue but its
     * work is enqueued, so it cannot interleave with a record or send.
     */
    private armTick(session: Session): void {
        if (session.phase === 'closed') return;
        session.tickTimer = this.timers.setTimeout(() => {
            session.tickTimer = undefined;
            if (session.phase === 'closed') return;
            this.enqueue(session, () => session.engine.onTick());
            this.armTick(session);
        }, SESSION_TICK_MS);
    }

    private cancelTick(session: Session): void {
        if (session.tickTimer === undefined) return;
        this.timers.clearTimeout(session.tickTimer);
        session.tickTimer = undefined;
    }

    /**
     * A link that is no longer connected (loss, user disconnect, ambiguous
     * send) ends that session only. Other phones are untouched.
     */
    private prune(snapshot: ConnectorSnapshot): void {
        for (const session of [...this.byHandle.values()]) {
            const connection = snapshot.connections.find(
                (candidate) => candidate.handle === session.handle,
            );
            if (connection !== undefined && connection.status === 'connected') continue;
            this.terminate(
                session,
                connection === undefined ? 'link_lost' : 'connection_closed',
                false,
            );
        }
    }

    private abortAll(): void {
        for (const session of [...this.byHandle.values()]) {
            this.terminate(session, 'port_closed', false);
        }
    }
}
