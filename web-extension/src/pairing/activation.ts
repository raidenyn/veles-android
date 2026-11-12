import {
    equalConstantTime,
    type ApplicationHandler,
    type SessionContext,
    type UnpromptedApplication,
} from '../session/engine';
import { SessionReject } from '../session/reject';
import { concat, hmacSha256, type RecoveryKeys } from '../session/schedule';
import { RecordType, SessionClass } from '../session/wire';
import { hex } from '../trust/model';

import type { ProofRegistry } from './proofs';

// OTP-10 plan 9d: the Chrome side of the activation exchange on a signed reconnect
// (OTP2-STA-010b steps 0, 5 and 6).
//
// A `pending` client never sends `activation_commit` before it verified this session's
// `enrollment_prepare`; an active-family client that sees `peer_state = 0x02` sends the step-5
// `activation_ack` unprompted on `authenticated`. Every received MAC is compared in constant
// time and the verified `activation_complete` is registered in the session-bound proof registry
// immediately before the repository transition, so the combined verifier accepts nothing else.

const AUTHENTICATOR_BYTES = 32;
const PEER_STATE_BELOW_ACTIVE = 0x02;
const STAGE_PREPARE = 0x00;
const STAGE_COMMIT = 0x01;
const STAGE_COMPLETE = 0x02;
const STAGE_ACK = 0x03;

/** The store surface the activation flow needs; `ChromeTrustRepository` satisfies it. */
export interface ActivationTrustRepository {
    peers(): Promise<readonly { installationId: string; state: string }[]>;
    confirm(peerId: string, stage: 'complete', proof: Uint8Array): Promise<void>;
}

export interface ActivationEngineOptions {
    trust: ActivationTrustRepository;
    proofs: ProofRegistry;
}

/** OTP2-STA-010b recovery activation handler for the client engine. */
export class ActivationEngine implements ApplicationHandler {
    readonly #trust: ActivationTrustRepository;
    readonly #proofs: ProofRegistry;

    constructor(options: ActivationEngineOptions) {
        this.#trust = options.trust;
        this.#proofs = options.proofs;
    }

    handles(type: number): boolean {
        return type === RecordType.ENROLLMENT_PREPARE || type === RecordType.ACTIVATION_COMPLETE;
    }

    handle(): Promise<void> {
        return Promise.reject(new SessionReject());
    }

    forSession(context: SessionContext): ApplicationHandler {
        return new SessionActivation(this.#trust, this.#proofs, context);
    }
}

class SessionActivation implements ApplicationHandler {
    readonly #trust: ActivationTrustRepository;
    readonly #proofs: ProofRegistry;
    readonly #context: SessionContext;
    readonly #sessionId: string;
    readonly #et: Uint8Array | null;
    readonly #recovery: RecoveryKeys | null;
    #commitSent = false;

    constructor(trust: ActivationTrustRepository, proofs: ProofRegistry, context: SessionContext) {
        this.#trust = trust;
        this.#proofs = proofs;
        this.#context = context;
        this.#sessionId = hex(context.sessionIdPrefix);
        this.#et = context.et === null ? null : context.et.slice();
        this.#recovery = context.recovery;
    }

    handles(type: number): boolean {
        return type === RecordType.ENROLLMENT_PREPARE || type === RecordType.ACTIVATION_COMPLETE;
    }

    async unprompted(): Promise<UnpromptedApplication[]> {
        const et = this.#et;
        const recovery = this.#recovery;
        if (et === null || recovery === null) return [];
        if (this.#context.sessionClass === SessionClass.FULL) return [];
        if (this.#context.peerState !== PEER_STATE_BELOW_ACTIVE) return [];
        // OTP2-STA-010b step 5: only an active-family record owes the ack. A genuinely
        // pending record is also `activation_recovery`, so the local state decides.
        const localState = await this.#localState();
        if (localState !== 'active' && localState !== 'active_activation_pending') return [];
        return [
            {
                type: RecordType.ACTIVATION_ACK,
                body: await activationMac(recovery.kRecClient, et, STAGE_ACK),
            },
        ];
    }

    async handle(
        type: number,
        body: Uint8Array,
        reply: (type: number, body: Uint8Array) => Promise<void>,
    ): Promise<void> {
        if (body.length !== AUTHENTICATOR_BYTES) throw new SessionReject();
        const et = this.#et;
        const recovery = this.#recovery;
        if (et === null || recovery === null) throw new SessionReject();
        if (type === RecordType.ENROLLMENT_PREPARE) {
            await this.#verify(recovery.kRecPhone, et, STAGE_PREPARE, body);
            // Step 0: only a genuinely pending client may re-commit. An active-family mirror
            // (also `activation_recovery`) already owes the step-5 ack and never commits here.
            if (this.#context.sessionClass !== SessionClass.ACTIVATION_RECOVERY) return;
            if ((await this.#localState()) !== 'pending') return;
            const commit = await activationMac(recovery.kRecClient, et, STAGE_COMMIT);
            try {
                await reply(RecordType.ACTIVATION_COMMIT, commit);
                this.#commitSent = true;
            } finally {
                commit.fill(0);
            }
            return;
        }
        if (type !== RecordType.ACTIVATION_COMPLETE) throw new SessionReject();
        await this.#verify(recovery.kRecPhone, et, STAGE_COMPLETE, body);
        // OTP2-STA-010b step 3: a still-pending client validates complete only after it sent
        // this session's commit. An active-family mirror owes the ack, not a commit.
        if ((await this.#localState()) === 'pending' && !this.#commitSent) {
            throw new SessionReject();
        }
        if (this.#context.sessionClass === SessionClass.ACTIVATION_RECOVERY) {
            this.#proofs.register(this.#sessionId, [
                { peerId: this.#context.peerId, stage: 'complete', proof: body.slice() },
            ]);
            try {
                await this.#trust.confirm(this.#context.peerId, 'complete', body.slice());
            } catch {
                this.#proofs.revoke(this.#sessionId);
                throw new SessionReject();
            }
        }
        const ack = await activationMac(recovery.kRecClient, et, STAGE_ACK);
        try {
            await reply(RecordType.ACTIVATION_ACK, ack);
        } finally {
            ack.fill(0);
        }
    }

    async #localState(): Promise<string | undefined> {
        const peers = await this.#trust.peers();
        return peers.find((peer) => peer.installationId === this.#context.peerId)?.state;
    }

    async #verify(
        key: Uint8Array,
        et: Uint8Array,
        stage: number,
        received: Uint8Array,
    ): Promise<void> {
        const expected = await activationMac(key, et, stage);
        try {
            if (!equalConstantTime(expected, received)) throw new SessionReject();
        } finally {
            expected.fill(0);
        }
    }
}

async function activationMac(key: Uint8Array, et: Uint8Array, stage: number): Promise<Uint8Array> {
    return hmacSha256(key, concat(et, Uint8Array.of(stage)));
}

/**
 * Composes the activation handler with an optional build-type extension handler (the debug
 * probe). A null extension never removes activation, matching the Kotlin `main` composition.
 */
export function composeHandlers(
    ...handlers: (ApplicationHandler | undefined)[]
): ApplicationHandler | undefined {
    const present = handlers.filter(
        (handler): handler is ApplicationHandler => handler !== undefined,
    );
    if (present.length === 0) return undefined;
    if (present.length === 1) return present[0];
    return new CompositeHandler(present);
}

class CompositeHandler implements ApplicationHandler {
    readonly #handlers: ApplicationHandler[];
    readonly #sessionHandlers: ApplicationHandler[];

    constructor(handlers: ApplicationHandler[]) {
        this.#handlers = handlers;
        this.#sessionHandlers = handlers;
    }

    handles(type: number): boolean {
        return this.#sessionHandlers.some((handler) => handler.handles(type));
    }

    async handle(
        type: number,
        body: Uint8Array,
        reply: (type: number, body: Uint8Array) => Promise<void>,
    ): Promise<void> {
        let handled = false;
        for (const handler of this.#sessionHandlers) {
            if (!handler.handles(type)) continue;
            await handler.handle(type, body, reply);
            handled = true;
        }
        if (!handled) throw new SessionReject();
    }

    forSession(context: SessionContext): ApplicationHandler {
        return new CompositeHandler(
            this.#handlers.map((handler) => handler.forSession?.(context) ?? handler),
        );
    }

    async unprompted(): Promise<UnpromptedApplication[]> {
        const lists = await Promise.all(
            this.#sessionHandlers.map((handler) => handler.unprompted?.() ?? []),
        );
        return lists.flat();
    }
}
