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

import { hex, type TrustedPeer } from '../trust/model';
import type { AuthenticationVerifier, ChromeTrustRepository } from '../trust/repository';
import {
    ClientSessionEngine,
    PEER_STATE_ACTIVE,
    STAGE_REMOVAL,
    type ClientEngineDeps,
    type SessionEffect,
} from '../session/engine';
import { QueueOverflow, SESSION_QUEUE_LIMIT, SerialQueue } from '../session/queue';
import { SessionReject } from '../session/reject';
import { RecordType, SessionClass } from '../session/wire';

import type { DiagnosticRing } from './diagnostics';
import type { ConnectorClose, ConnectorResult } from './port';
import type { WireEvent } from './protocol';
import type { ConnectorSnapshot } from './state';
import type { PairingLink } from '../pairing/link';

export type SentResult = ConnectorResult<Extract<WireEvent, { type: 'sent' }>>;

/**
 * The requester's mutual-removal outcome (design D4, OTP2-REC-013).
 *
 * `removed` means a valid `removal_response` deleted the local record; `kept-not-connected`
 * means there was no FULL session of that peer to ask; `kept-no-response` means the exchange
 * produced no valid response — a lost response, a disconnect, the deadline or a protocol
 * violation — so the local record is kept and the other endpoint may keep its own.
 */
export type RemovalResult = 'removed' | 'kept-not-connected' | 'kept-no-response';

/**
 * The requester's `removal_response` deadline (design call, not a protocol value; design D4). It
 * is armed on the injected `SessionTimers`, because the engine's 15 000 ms tick is longer.
 */
export const REMOVAL_RESPONSE_TIMEOUT_MS = 10_000;

/** The owner-owned removal deadline diagnostic code. */
const REMOVAL_TIMEOUT_REASON = 'removal_timeout';

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

/** The settle seam of one requester removal exchange (design D4): the first outcome wins. */
interface RemovalExchange {
    settle(result: RemovalResult): void;
}

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
    /** The requester's in-flight removal exchange, or null. */
    removal: RemovalExchange | null;
    /** The pending `removal_response` deadline, cancelled on response, disconnect and teardown. */
    removalTimer: unknown;
    /** Resolves once the logical termination ran and the engine erased its keys. */
    readonly closed: Promise<void>;
    resolveClosed(): void;
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
        /** Session-bound proof cleanup when a generation is torn down. */
        private readonly onSessionClosed?: (sessionId: string) => void,
        /**
         * Live enrollment links, so a peer revocation also terminates the matching
         * enrollment connection (design D3). Selection reads only `boundPeerId()`.
         */
        private readonly links: () => readonly PairingLink[] = () => [],
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
        let resolveClosed!: () => void;
        const closed = new Promise<void>((resolve) => {
            resolveClosed = resolve;
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
            removal: null,
            removalTimer: undefined,
            closed,
            resolveClosed,
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
     * Asks the peer to remove this endpoint's record (OTP2-REC-013, design D4).
     *
     * The request runs on the serialized queue of the peer's FULL session: the engine seals
     * `removal_request`, then the removal-pending guard is set on that session only and the
     * response deadline is armed on the injected timers. This manager never deletes anything
     * itself; the removal handler does that inside the validated response's step.
     *
     * `removed` is returned only when the session ended through the engine's `removal` stage.
     * Every other end — a lost response, a disconnect, `REMOVAL_RESPONSE_TIMEOUT_MS` or a
     * protocol violation — returns `kept-no-response`; without a live FULL session of `peerId`
     * the call returns `kept-not-connected`. A second request while one is already awaiting a
     * response returns `kept-no-response` without sending anything.
     */
    requestRemoval(peerId: string): Promise<RemovalResult> {
        const session = [...this.byHandle.values()].find(
            (candidate) =>
                candidate.peerId === peerId &&
                candidate.phase === 'open' &&
                candidate.engine.sessionClass === SessionClass.FULL,
        );
        if (session === undefined) return Promise.resolve('kept-not-connected');
        if (session.removal !== null) return Promise.resolve('kept-no-response');
        let settle!: (result: RemovalResult) => void;
        const result = new Promise<RemovalResult>((resolve) => {
            settle = resolve;
        });
        // Registered before the step can run, so a disconnect or teardown always settles it.
        session.removal = { settle };
        const started = session.queue.run(() => this.startRemoval(session));
        void started.catch((error: unknown) => {
            this.settleRemoval(session, 'kept-no-response');
            // A queue refusal sent nothing; any other failure ends the session like a queue step.
            if (!(error instanceof QueueOverflow)) this.terminate(session, 'dependency', true);
        });
        return result;
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

    /**
     * Terminates every session and enrollment link bound to one authenticated peer
     * (design D3). Selection uses only the peer installation id — never a label, a
     * native handle or a platform hint — so another phone and an unbound enrollment
     * are untouched. Each session's termination is queued behind that session's
     * in-flight step, so a handler's buffered reply is delivered before the close,
     * and the promise resolves only after every selected generation has ended.
     * Logical termination is authoritative: a failing transport close is best-effort
     * and never aborts the termination of another selected session.
     */
    async closePeer(peerId: string): Promise<void> {
        await this.closeSelected(
            [...this.byHandle.values()].filter((session) => session.peerId === peerId),
            this.links().filter((link) => link.boundPeerId() === peerId),
            'revoked',
        );
    }

    /**
     * Terminates every session and enrollment link without touching durable trust
     * records (design D2/R8): the non-destructive teardown used by fail-closed and a
     * reset, distinct from the destructive pairing `cancel()`.
     */
    async closeAll(): Promise<void> {
        await this.closeSelected([...this.byHandle.values()], [...this.links()], 'closed');
    }

    private async closeSelected(
        sessions: readonly Session[],
        links: readonly PairingLink[],
        reason: string,
    ): Promise<void> {
        // Every selected generation is terminated even when one link close fails; the
        // first failure is reported only after all of them settled.
        const results = await Promise.allSettled([
            ...sessions.map((session) => this.closeBehind(session, reason)),
            ...links.map((link) => link.terminate()),
        ]);
        for (const result of results) {
            if (result.status === 'rejected') throw result.reason;
        }
    }

    /**
     * Queues one logical termination behind the session's in-flight step. A queue
     * that refuses the job must not leave an authorized session alive, so the
     * teardown still runs and only the refusal is reported afterwards.
     */
    private closeBehind(session: Session, reason: string): Promise<void> {
        const queued = session.queue.run(async () => {
            this.terminate(session, reason, true);
            await session.closed;
        });
        return queued.catch((error: unknown) => {
            this.terminate(session, reason, true);
            if (error instanceof QueueOverflow) return session.closed;
            throw error;
        });
    }

    private enqueue(session: Session, job: () => Promise<SessionEffect[]>): Promise<void> {
        const generation = this.portGeneration();
        const task = session.queue.run(async () => {
            const effects = await job();
            await this.apply(session, generation, effects);
            this.settleSealedRemoval(session, effects);
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
        this.cancelRemovalDeadline(session);
        // Design D4/R6: only the engine's removal stage proves the handler deleted the local
        // record. Every other end keeps it — deferred behind the in-flight step, because a
        // handler that is deleting right now can still prove the deletion.
        if (reason === STAGE_REMOVAL) {
            this.settleRemoval(session, 'removed');
        } else {
            this.settleRemovalBehind(session, 'kept-no-response');
        }
        const prefix = session.engine.sessionIdPrefix;
        if (prefix !== null) this.onSessionClosed?.(hex(prefix));
        session.engine.teardown();
        if (this.byHandle.get(session.handle) === session) this.byHandle.delete(session.handle);
        session.resolveClosed();
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
     * Seals the requester's `removal_request` inside the peer's serialized session step and arms
     * the guard and the deadline in that same step. The request is sealed before the guard is
     * set, so the request itself is never rejected by it.
     */
    private async startRemoval(session: Session): Promise<void> {
        if (session.removal === null) return;
        const generation = this.portGeneration();
        if (
            session.phase !== 'open' ||
            session.engine.sessionClass !== SessionClass.FULL ||
            !this.isCurrent(session, generation)
        ) {
            this.settleRemoval(session, 'kept-no-response');
            return;
        }
        let effects: SessionEffect[];
        try {
            effects = await session.engine.sendApplication(
                RecordType.REMOVAL_REQUEST,
                new Uint8Array(0),
            );
        } catch {
            this.settleRemoval(session, 'kept-no-response');
            this.terminate(session, 'dependency', true);
            return;
        }
        if (session.removal === null) return;
        session.engine.setRemovalPending(true);
        this.armRemovalDeadline(session);
        if (!this.isCurrent(session, generation)) {
            // The session is already gone: the request cannot be delivered from here.
            this.settleRemoval(session, 'kept-no-response');
            return;
        }
        await this.apply(session, generation, effects);
    }

    /**
     * Arms the requester's response deadline. The engine tick is 15 000 ms, longer than
     * `REMOVAL_RESPONSE_TIMEOUT_MS`, so this owner-owned timer enforces it; its expiry waits for
     * the work already submitted, so a response or a deletion that is completing right now
     * settles first.
     */
    private armRemovalDeadline(session: Session): void {
        this.cancelRemovalDeadline(session);
        session.removalTimer = this.timers.setTimeout(() => {
            session.removalTimer = undefined;
            if (session.removal === null) return;
            // `drained()` takes no queue slot: a saturated queue must not settle the exchange
            // before an in-flight deletion can prove its outcome.
            void session.queue.drained().then(() => this.expireRemoval(session));
        }, REMOVAL_RESPONSE_TIMEOUT_MS);
    }

    /** The deadline outcome: an expired exchange keeps the record and fails the session. */
    private expireRemoval(session: Session): void {
        if (session.removal === null) return;
        this.settleRemoval(session, 'kept-no-response');
        this.terminate(session, REMOVAL_TIMEOUT_REASON, true);
    }

    private cancelRemovalDeadline(session: Session): void {
        if (session.removalTimer === undefined) return;
        this.timers.clearTimeout(session.removalTimer);
        session.removalTimer = undefined;
    }

    /** Settles the session's in-flight removal exchange exactly once: the first outcome wins. */
    private settleRemoval(session: Session, outcome: RemovalResult): void {
        const exchange = session.removal;
        if (exchange === null) return;
        session.removal = null;
        this.cancelRemovalDeadline(session);
        exchange.settle(outcome);
    }

    /**
     * Settles an exchange from a session end. The settlement waits for the session's in-flight
     * step, because a removal handler deleting right now can still prove the deletion; an idle
     * queue settles at once. It uses `drained()` rather than another queue job, so a saturated
     * queue can never settle the exchange before that deletion completes.
     */
    private settleRemovalBehind(session: Session, outcome: RemovalResult): void {
        if (session.removal === null) return;
        if (session.queue.pending === 0) {
            this.settleRemoval(session, outcome);
            return;
        }
        void session.queue.drained().then(() => this.settleRemoval(session, outcome));
    }

    /**
     * Design D4/R6: a removal stage the engine sealed still proves the handler deleted the local
     * record even when an external teardown dropped the step's effects before they were applied.
     */
    private settleSealedRemoval(session: Session, effects: SessionEffect[]): void {
        const dropped = session.engine.takeDroppedTerminalStage();
        if (session.removal === null) return;
        let sealed = dropped;
        if (sealed !== STAGE_REMOVAL) {
            for (let index = effects.length - 1; index >= 0; index -= 1) {
                const effect = effects[index];
                if (effect.kind !== 'terminated') continue;
                sealed = effect.reason;
                break;
            }
        }
        if (sealed !== STAGE_REMOVAL) return;
        this.settleRemoval(session, 'removed');
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
