// Connector-side session owner: one client engine per phone link.
//
// The connector no longer treats a transport `connected` event as success. A
// successful `connect` hands the connection handle and one intended trust
// record id to this manager, which:
//   - runs the §11.3 client-role engine for that handle,
//   - serializes every inbound record and outbound request through one bounded
//     queue (§11.3, OTP2-REC-034 queue bound),
//   - drops any completion whose handle or port generation changed,
//   - answers the trust repository's stage-bound verifier itself, and
//   - records `lastSeen`/the platform hint only after the gate opened for the
//     current generation.
//
// Discovery metadata never supplies the peer id: recovery passes the target's
// id and manual Connect passes an explicit id or the single active-family
// trusted phone (`intendedPeer`).

import type { TrustedPeer } from '../trust/model';
import type { AuthenticationVerifier, ChromeTrustRepository } from '../trust/repository';
import { ClientSessionEngine, type ClientEngineDeps, type SessionEffect } from '../session/engine';
import { QueueOverflow, SESSION_QUEUE_LIMIT, SerialQueue } from '../session/queue';
import { SessionReject } from '../session/reject';
import { Direction, RecordType } from '../session/wire';

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

type SessionPhase = 'handshake' | 'open' | 'closed';

interface Session {
    readonly handle: string;
    readonly peerId: string;
    /** The port generation this session was opened on. */
    readonly generation: number;
    readonly engine: ClientSessionEngine;
    readonly queue: SerialQueue;
    phase: SessionPhase;
    /** The verified phone `reconnect_confirm` bytes, used as the stage proof. */
    proof: Uint8Array | null;
    settled: boolean;
    resolve(result: SessionOpenResult): void;
}

const PROTOCOL_VERSION = 1;
const SCHEMA_VERSION = 1;
const RECORD_HEADER_BYTES = 23;
const RECORD_TAG_BYTES = 16;
const CONFIRM_BODY_BYTES = 33;
const SEQUENCE_OFFSET = 5;
const TYPE_OFFSET = 3;
const CONFIRM_SEQUENCE_BYTES = 8;

function equalBytes(left: Uint8Array, right: Uint8Array): boolean {
    if (left.length !== right.length) return false;
    let difference = 0;
    for (let index = 0; index < left.length; index += 1) {
        difference |= left[index] ^ right[index];
    }
    return difference === 0;
}

/**
 * True for a protected phone→client `reconnect_confirm` record at sequence 0
 * with the exact confirm body length. The engine is the only component that
 * decides whether such a record is valid; this check only lets the manager
 * retain the exact bytes the engine later authenticated, so the trust
 * repository's stage verifier can compare them.
 */
function isConfirmRecord(bytes: Uint8Array): boolean {
    if (bytes.length !== RECORD_HEADER_BYTES + CONFIRM_BODY_BYTES + RECORD_TAG_BYTES) {
        return false;
    }
    if (bytes[0] !== PROTOCOL_VERSION || bytes[1] !== SCHEMA_VERSION) return false;
    if (bytes[2] !== Direction.PHONE_TO_CLIENT) return false;
    const type = ((bytes[TYPE_OFFSET] ?? 0) << 8) | (bytes[TYPE_OFFSET + 1] ?? 0);
    if (type !== RecordType.RECONNECT_CONFIRM) return false;
    for (let index = 0; index < CONFIRM_SEQUENCE_BYTES; index += 1) {
        if (bytes[SEQUENCE_OFFSET + index] !== 0) return false;
    }
    return true;
}

function activeFamily(peer: TrustedPeer): boolean {
    return peer.state === 'active' || peer.state === 'active_activation_pending';
}

export class SessionManager {
    readonly repository: ChromeTrustRepository;
    readonly verifier: AuthenticationVerifier;

    private readonly byHandle = new Map<string, Session>();

    constructor(
        private readonly controller: SessionController,
        repository: ChromeTrustRepository,
        private readonly deps: (handle: string, peerId: string) => ClientEngineDeps,
        private readonly portGeneration: () => number,
    ) {
        this.repository = repository;
        this.verifier = async (peer, stage, proof) => {
            if (stage !== 'phone_active' && stage !== 'authenticated') return false;
            for (const session of this.byHandle.values()) {
                if (session.peerId !== peer.installationId) continue;
                if (!this.isCurrent(session, session.generation)) continue;
                if (session.proof === null || !equalBytes(session.proof, proof)) continue;
                if (stage === 'phone_active' && session.phase === 'handshake') return true;
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
            proof: null,
            settled: false,
            resolve: settle,
        };
        this.byHandle.set(handle, session);
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
                        const token = effect.completionToken;
                        this.enqueue(session, () => session.engine.onSendComplete(token, sent.ok));
                    } else if (!sent.ok) {
                        this.terminate(session, 'send_failed', true);
                        return;
                    }
                    break;
                }
                case 'authenticated': {
                    await this.authenticated(session, effect.peerId);
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
        const proof = session.proof;
        if (proof === null || !this.isCurrent(session, session.generation)) {
            this.terminate(session, 'authentication', true);
            return;
        }
        const hint = this.connectionHint(session.handle);
        try {
            await this.repository.authenticated(peerId, hint, proof);
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
        session.engine.teardown();
        if (this.byHandle.get(session.handle) === session) this.byHandle.delete(session.handle);
        session.proof?.fill(0);
        session.proof = null;
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
        if (isConfirmRecord(record.bytes)) session.proof = record.bytes.slice();
        const bytes = record.bytes.slice();
        this.enqueue(session, () => session.engine.onInbound(bytes));
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
