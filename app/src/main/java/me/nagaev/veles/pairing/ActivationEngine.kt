package me.nagaev.veles.pairing

import kotlinx.coroutines.CancellationException
import me.nagaev.veles.session.ApplicationHandler
import me.nagaev.veles.session.RecordType
import me.nagaev.veles.session.SessionClass
import me.nagaev.veles.session.SessionContext
import me.nagaev.veles.session.SessionReject
import me.nagaev.veles.session.UnpromptedApplication
import me.nagaev.veles.trust.ActivationStep
import me.nagaev.veles.trust.InstallationTrustRepository
import me.nagaev.veles.trust.TrustEncoding
import me.nagaev.veles.trust.TrustState
import me.nagaev.veles.trust.TrustedPeer
import java.security.GeneralSecurityException
import java.security.MessageDigest
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * The phone-side activation exchange of a signed reconnect (OTP2-STA-010b steps 0, 5 and 6).
 *
 * The [SessionContext] fixes the class, the peer, `ET` and the live OTP2-CRY-019f recovery
 * keys. The handler:
 *  - owes `enrollment_prepare` (stage `0x00`, `K_rec_phone`) whenever the client reported
 *    `peer_state = 0x02`, including `full_pending_peer` (step 0);
 *  - verifies `activation_commit`/`activation_ack` (stages `0x01`/`0x03`, `K_rec_client`) with a
 *    constant-time comparison, registers the verified MAC for exactly this session and only then
 *    asks the trust repository to transition, so the production verifier accepts nothing else;
 *  - re-sends `activation_complete` (stage `0x02`, `K_rec_phone`) after every verified commit,
 *    including the idempotent duplicate in `full_pending_peer`;
 *  - never changes a session's fixed class: a recovery session stays recovery-only through its
 *    own teardown, even when the record becomes active.
 */
class ActivationEngine(
    private val trust: InstallationTrustRepository,
    private val proofs: ProofRegistry = ProofRegistry(),
) : ApplicationHandler {
    override fun handles(type: Int): Boolean = type == RecordType.ACTIVATION_COMMIT.wire || type == RecordType.ACTIVATION_ACK.wire

    override suspend fun handle(
        type: Int,
        body: ByteArray,
        reply: suspend (type: Int, body: ByteArray) -> Unit,
    ): Unit = throw SessionReject()

    override fun forSession(context: SessionContext): ApplicationHandler = SessionActivation(trust, proofs, context)
}

private class SessionActivation(
    private val trust: InstallationTrustRepository,
    private val proofs: ProofRegistry,
    context: SessionContext,
) : ApplicationHandler {
    private val sessionId = TrustEncoding.hex(context.sessionIdPrefix)
    private val peerId = context.peerId
    private val sessionClass = context.sessionClass
    private val peerState = context.peerState
    private val et = context.et?.copyOf()
    private val recovery = context.recovery

    override fun handles(type: Int): Boolean = type == RecordType.ACTIVATION_COMMIT.wire || type == RecordType.ACTIVATION_ACK.wire

    @Suppress("ReturnCount") // One guard per missing session fact.
    override fun unprompted(): List<UnpromptedApplication> {
        if (peerState != PEER_STATE_BELOW_ACTIVE) return emptyList()
        if (sessionClass == SessionClass.FULL) return emptyList()
        val transcript = et ?: return emptyList()
        val phoneKey = recovery?.kRecPhone ?: return emptyList()
        return listOf(
            UnpromptedApplication(
                RecordType.ENROLLMENT_PREPARE.wire,
                activationMac(phoneKey, transcript, STAGE_PREPARE),
            ),
        )
    }

    @Suppress("ReturnCount") // One guard per missing session fact.
    override suspend fun handle(
        type: Int,
        body: ByteArray,
        reply: suspend (type: Int, body: ByteArray) -> Unit,
    ) {
        if (body.size != AUTHENTICATOR_BYTES) throw SessionReject()
        val transcript = et ?: throw SessionReject()
        val clientKey = recovery?.kRecClient ?: throw SessionReject()
        val step = if (type == RecordType.ACTIVATION_COMMIT.wire) ActivationStep.COMMIT else ActivationStep.ACK
        val stage = if (type == RecordType.ACTIVATION_COMMIT.wire) STAGE_COMMIT else STAGE_ACK
        val expected = activationMac(clientKey, transcript, stage)
        val matches = try {
            MessageDigest.isEqual(expected, body)
        } finally {
            expected.fill(0)
        }
        // OTP2-CRY-019g / states.json: a wrong activation MAC on an incomplete record deletes
        // it before the session ends, so no half-activated trust state survives.
        if (!matches) {
            eraseIncomplete()
            throw SessionReject()
        }
        val peer = trust.peers().firstOrNull { it.installationId == peerId } ?: throw SessionReject()
        if (!applyVerified(peer, step, body)) throw SessionReject()
        if (step == ActivationStep.COMMIT) {
            val complete = activationMac(recovery.kRecPhone, transcript, STAGE_COMPLETE)
            try {
                reply(RecordType.ACTIVATION_COMPLETE.wire, complete)
            } finally {
                complete.fill(0)
            }
        }
    }

    /** Deletes only an incomplete record of this peer; an active record is never touched. */
    private suspend fun eraseIncomplete() {
        val peer = try {
            trust.peers().firstOrNull { it.installationId == peerId }
        } catch (cancellation: CancellationException) {
            throw cancellation
        } catch (_: Exception) {
            return
        } ?: return
        if (peer.state == TrustState.ACTIVE) return
        try {
            trust.revoke(peerId)
        } catch (cancellation: CancellationException) {
            throw cancellation
        } catch (_: Exception) {
            // The session still fails closed; a store failure leaves no usable record.
        }
    }

    /**
     * Applies a MAC-verified step through the repository (which verifies the same proof again via
     * [ProofRegistry]). A duplicate commit on an already committed record is a semantic no-op and
     * never reaches the store; the `ACTIVE` no-op is allowed only in `full_pending_peer` and is
     * the store's own 9c rule.
     */
    private suspend fun applyVerified(peer: TrustedPeer, step: ActivationStep, body: ByteArray): Boolean {
        val expected = if (step == ActivationStep.COMMIT) TrustState.PENDING else TrustState.COMMITTED_WAITING_ACK
        val fullPendingNoop = sessionClass == SessionClass.FULL_PENDING_PEER && peer.state == TrustState.ACTIVE
        val storeWrite = peer.state == expected || fullPendingNoop
        if (!storeWrite) {
            // OTP2-STA-010b step 1: a committed phone holds the state; only a re-send follows.
            return step == ActivationStep.COMMIT && peer.state == TrustState.COMMITTED_WAITING_ACK
        }
        proofs.register(sessionId, listOf(ActivationProof(peerId, step, body)))
        return try {
            trust.confirm(peerId, step, body.copyOf())
            true
        } catch (_: Exception) {
            proofs.revoke(sessionId)
            false
        }
    }

    private fun activationMac(key: ByteArray, transcript: ByteArray, stage: Byte): ByteArray = try {
        Mac.getInstance(HMAC_ALGORITHM).run {
            init(SecretKeySpec(key, HMAC_ALGORITHM))
            doFinal(transcript + byteArrayOf(stage))
        }
    } catch (_: GeneralSecurityException) {
        throw SessionReject()
    }

    private companion object {
        const val AUTHENTICATOR_BYTES = 32
        const val PEER_STATE_BELOW_ACTIVE = 0x02
        const val HMAC_ALGORITHM = "HmacSHA256"
        const val STAGE_PREPARE: Byte = 0x00
        const val STAGE_COMMIT: Byte = 0x01
        const val STAGE_COMPLETE: Byte = 0x02
        const val STAGE_ACK: Byte = 0x03
    }
}
