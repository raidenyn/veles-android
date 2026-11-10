// Bounded serialized work for one reconnect session.
//
// The §11.3 client engine is a pure state machine whose sequence and nonce
// allocation is synchronous, but every cryptographic step (signature verify,
// ECDH, HKDF, record seal/open) is asynchronous. The connector must therefore
// run each inbound record and each outbound request to completion, in arrival
// order, so two adjacent frames can never interleave and two concurrent sends
// can never share or skip a sequence.
//
// The queue is bounded. Overflow is a terminal session condition, reported by
// `QueueOverflow`; the session owner ends the session with
// `resource_exhausted` rather than blocking or dropping work silently.

/** Per-peer queued-message cap from the reconnect session rules. */
export const SESSION_QUEUE_LIMIT = 16;

/** A job was submitted while the queue already held its maximum. */
export class QueueOverflow extends Error {
    constructor() {
        super('session queue overflow');
        this.name = 'QueueOverflow';
    }
}

export class SerialQueue {
    private tail: Promise<void> = Promise.resolve();
    private queued = 0;

    constructor(private readonly limit: number = SESSION_QUEUE_LIMIT) {
        if (!Number.isInteger(limit) || limit < 1) {
            throw new Error('invalid session queue limit');
        }
    }

    /** Jobs currently submitted and not yet finished (including the running one). */
    get pending(): number {
        return this.queued;
    }

    /**
     * Submits one job. A job starts only after the previous job settled; a
     * rejected job does not break the chain, so the caller alone decides what
     * a failure means.
     */
    run<T>(job: () => Promise<T>): Promise<T> {
        if (this.queued >= this.limit) return Promise.reject(new QueueOverflow());
        this.queued += 1;
        const result = this.tail.then(job);
        this.tail = result.then(
            () => {
                this.queued -= 1;
            },
            () => {
                this.queued -= 1;
            },
        );
        return result;
    }
}
