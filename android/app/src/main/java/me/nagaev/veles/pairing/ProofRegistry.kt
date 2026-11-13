package me.nagaev.veles.pairing

import me.nagaev.veles.trust.ActivationStep
import me.nagaev.veles.trust.TrustedPeer
import java.security.MessageDigest

/**
 * One activation proof a pairing machine or a recovery session registers for exactly one
 * session (OTP2-STA-010b). The MAC is a copy owned by the registry and zeroed when consumed.
 */
data class ActivationProof(
    val peerId: String,
    val step: ActivationStep,
    val mac: ByteArray,
)

/**
 * Session-bound proof registry consulted by the production phone verifier.
 *
 * The trust repository's [me.nagaev.veles.trust.ActivationVerifier] is fixed when the repository
 * is built, so it cannot receive a per-session reference directly. The pairing machine and the
 * recovery activation engine register the stage MAC they computed for the current session here;
 * [consume] accepts only a registered peer/step/MAC triple and removes it, so a proof authorizes
 * exactly one store transition and a replay from another session (whose fresh keys produce a
 * different MAC) never verifies.
 *
 * Every MAC comparison is constant-time. [revoke] erases the session's remaining entries when the
 * session ends.
 */
class ProofRegistry {
    private class Entry(
        val sessionId: String,
        val peerId: String,
        val step: ActivationStep,
        val mac: ByteArray,
    )

    private val lock = Any()
    private val entries = mutableListOf<Entry>()

    /** Copies [proofs] under [sessionId]; the caller keeps ownership of its own buffers. */
    fun register(sessionId: String, proofs: List<ActivationProof>) {
        synchronized(lock) {
            proofs.forEach { entries += Entry(sessionId, it.peerId, it.step, it.mac.copyOf()) }
        }
    }

    /**
     * Consumes the first entry matching [peer], [step] and the constant-time [proof]. A mismatch
     * leaves every entry in place; only a successful match is single-use.
     */
    fun consume(peer: TrustedPeer, step: ActivationStep, proof: ByteArray): Boolean {
        synchronized(lock) {
            val index = entries.indexOfFirst { entry ->
                entry.peerId == peer.installationId &&
                    entry.step == step &&
                    MessageDigest.isEqual(entry.mac, proof)
            }
            if (index < 0) return false
            entries.removeAt(index).mac.fill(0)
            return true
        }
    }

    /** Erases and removes every proof registered for [sessionId]. */
    fun revoke(sessionId: String) {
        synchronized(lock) {
            val iterator = entries.iterator()
            while (iterator.hasNext()) {
                val entry = iterator.next()
                if (entry.sessionId == sessionId) {
                    entry.mac.fill(0)
                    iterator.remove()
                }
            }
        }
    }
}
