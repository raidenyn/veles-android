import { equalConstantTime } from '../session/engine';
import type { ProofStage } from '../trust/model';

/** One activation proof a pairing machine or a recovery session registers for one session. */
export interface ActivationProof {
    readonly peerId: string;
    readonly stage: ProofStage;
    readonly proof: Uint8Array;
}

interface Entry extends ActivationProof {
    readonly sessionId: string;
}

/**
 * Session-bound proof registry consulted by the connector's combined verifier.
 *
 * `ChromeTrustRepository` fixes its verifier when it is built, so the pairing machine and the
 * recovery activation engine register the stage MAC they computed for the current session here.
 * `consume` accepts only a registered peer/stage/proof triple and removes it, so a proof
 * authorizes exactly one store transition and a replay from another session (fresh keys, a
 * different MAC) never verifies. Every comparison is constant-time.
 */
export class ProofRegistry {
    readonly #entries: Entry[] = [];

    /** Copies [proofs] under [sessionId]; the caller keeps ownership of its own buffers. */
    register(sessionId: string, proofs: readonly ActivationProof[]): void {
        for (const proof of proofs) {
            this.#entries.push({
                sessionId,
                peerId: proof.peerId,
                stage: proof.stage,
                proof: proof.proof.slice(),
            });
        }
    }

    /** Consumes the first matching entry; a mismatch leaves every entry in place. */
    consume(peerId: string, stage: ProofStage, proof: Uint8Array): boolean {
        const index = this.#entries.findIndex(
            (entry) =>
                entry.peerId === peerId &&
                entry.stage === stage &&
                equalConstantTime(entry.proof, proof),
        );
        if (index < 0) return false;
        const [entry] = this.#entries.splice(index, 1);
        entry?.proof.fill(0);
        return true;
    }

    /** Erases and removes every proof registered for [sessionId]. */
    revoke(sessionId: string): void {
        for (let index = this.#entries.length - 1; index >= 0; index -= 1) {
            const entry = this.#entries[index];
            if (entry !== undefined && entry.sessionId === sessionId) {
                entry.proof.fill(0);
                this.#entries.splice(index, 1);
            }
        }
    }
}
