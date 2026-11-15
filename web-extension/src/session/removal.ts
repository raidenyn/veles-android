import type { ApplicationHandler, SessionContext } from './engine';
import { SessionReject } from './reject';
import { RecordType } from './wire';

/**
 * Deletes one peer's trust record for a handler-driven removal (design D2/D4/D8).
 *
 * An implementation takes the per-peer revocation fence, starts the coordinator's detached
 * finish job — so the fence outlives the handler's return and the current generation's
 * teardown — and then deletes the record. A store failure is raised as `TrustStoreError`; the
 * repository's store-failure callback is what escalates it to the fail-closed action, and the
 * engine's own failure path ends this session.
 *
 * The handler never awaits its own generation: awaiting it would deadlock on the step the
 * handler is running under.
 */
export interface RemovalOperations {
    deleteFenced(peerId: string): Promise<void>;
}

/** True for the two mutual-removal record types (OTP2-REC-013, FULL class only). */
export function isRemovalRecord(type: number): boolean {
    return type === RecordType.REMOVAL_REQUEST || type === RecordType.REMOVAL_RESPONSE;
}

/**
 * The mutual-removal handler (design D4, OTP2-REC-013): `removal_request` (0x0205) and
 * `removal_response` (0x0206), empty bodies, FULL class only. The record policy and the codec
 * already enforce both rules, so this handler only owns the trust and reply-then-close order.
 *
 * - Responder: on `removal_request`, delete the record of the authenticated peer first, then
 *   reply `removal_response`. It asks for the reply-then-close outcome, so the response is the
 *   last record in that direction and the engine performs the terminal teardown; the handler
 *   never tears the session down itself.
 * - Requester: the engine delivers `removal_response` only while the session is awaiting one
 *   (`ClientSessionEngine.setRemovalPending`); an unsolicited response is a protocol violation
 *   that the engine ends before this handler runs, so it never deletes anything. On a delivered
 *   response the handler deletes the local record and asks to close after the empty reply set.
 *
 * The session-scoped instance is bound to the authenticated peer id the engine fixed at the
 * authentication gate; no label, native handle or platform hint is ever an input.
 */
export function createRemovalHandler(ops: RemovalOperations): ApplicationHandler {
    return new RemovalHandler(ops);
}

class RemovalHandler implements ApplicationHandler {
    readonly #ops: RemovalOperations;

    constructor(ops: RemovalOperations) {
        this.#ops = ops;
    }

    handles(type: number): boolean {
        return isRemovalRecord(type);
    }

    /** The shared instance handles no record: the engine always asks for the session instance. */
    handle(): Promise<void> {
        return Promise.reject(new SessionReject());
    }

    forSession(context: SessionContext): ApplicationHandler {
        return new SessionRemoval(this.#ops, context.peerId);
    }
}

/** One session's removal handler, bound to the peer the engine authenticated. */
class SessionRemoval implements ApplicationHandler {
    readonly #ops: RemovalOperations;
    readonly #peerId: string;

    constructor(ops: RemovalOperations, peerId: string) {
        this.#ops = ops;
        this.#peerId = peerId;
    }

    handles(type: number): boolean {
        return isRemovalRecord(type);
    }

    async handle(
        type: number,
        _body: Uint8Array,
        reply: (type: number, body: Uint8Array) => Promise<void>,
    ): Promise<void> {
        // Delete first (design D2): a failed delete throws before any reply, so the peer never
        // sees a response for a record this endpoint still holds.
        await this.#ops.deleteFenced(this.#peerId);
        if (type === RecordType.REMOVAL_REQUEST) {
            await reply(RecordType.REMOVAL_RESPONSE, new Uint8Array(0));
        }
    }

    closeAfterReply(type: number): boolean {
        return isRemovalRecord(type);
    }
}
