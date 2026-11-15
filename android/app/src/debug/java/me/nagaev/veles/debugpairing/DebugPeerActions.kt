package me.nagaev.veles.debugpairing

import kotlinx.coroutines.CancellationException
import me.nagaev.veles.R
import me.nagaev.veles.debugprobe.DebugRevocationRuntime
import me.nagaev.veles.debugprobe.PeerManagement
import me.nagaev.veles.sharing.RemovalResult
import me.nagaev.veles.sharing.SharingState
import me.nagaev.veles.trust.PeerSummary
import me.nagaev.veles.trust.TrustException
import me.nagaev.veles.trust.TrustStatus

/**
 * What a debug peer action did, before it becomes screen copy. No key material, no code and no
 * peer identifier travels in an outcome.
 */
internal enum class PeerActionOutcome {
    DONE,

    /** Mutual remove found no open full session: only local removal is possible, nothing deleted. */
    LOCAL_ONLY,

    /** The removal request went out but the response never arrived; the record is kept. */
    NO_RESPONSE,

    /** A destructive action was not confirmed; nothing was touched. */
    CONFIRMATION_REQUIRED,
    FAILED,

    /** The sharing service (and with it the coordinator) is not running. */
    UNAVAILABLE,
}

/**
 * The peer-management logic behind the debug pairing screen (OTP-11 design D9). Every action is
 * the revocation coordinator's own operation; the screen only maps the outcome to copy and
 * re-reads the list. A trust-store failure is an outcome ([PeerActionOutcome.FAILED]) and never
 * an exception on the UI thread.
 */
internal class DebugPeerActions(
    private val management: () -> PeerManagement?,
    private val status: () -> TrustStatus,
) {
    fun trustStatus(): TrustStatus = status()

    /** The peers as the repository holds them now; `null` when they cannot be read. */
    suspend fun peers(): List<PeerSummary>? = guarded { management()?.peers() }

    suspend fun revoke(peerId: String): PeerActionOutcome = peerAction(peerId) { management ->
        management.revoke(peerId)
        PeerActionOutcome.DONE
    }

    suspend fun removeMutually(peerId: String): PeerActionOutcome = peerAction(peerId) { management ->
        when (management.removeMutually(peerId)) {
            RemovalResult.REMOVED -> PeerActionOutcome.DONE
            RemovalResult.KEPT_NOT_CONNECTED -> PeerActionOutcome.LOCAL_ONLY
            RemovalResult.KEPT_NO_RESPONSE -> PeerActionOutcome.NO_RESPONSE
        }
    }

    /**
     * The endpoint-wide reset. Without an explicit confirmation it touches nothing at all, so a
     * mis-tap can never delete the trust store or the identity.
     */
    suspend fun reset(confirmed: Boolean): PeerActionOutcome {
        if (!confirmed) return PeerActionOutcome.CONFIRMATION_REQUIRED
        val current = management() ?: return PeerActionOutcome.UNAVAILABLE
        return guarded {
            current.reset()
            PeerActionOutcome.DONE
        } ?: PeerActionOutcome.FAILED
    }

    private suspend fun peerAction(
        peerId: String,
        block: suspend (PeerManagement) -> PeerActionOutcome,
    ): PeerActionOutcome {
        if (peerId.isEmpty()) return PeerActionOutcome.FAILED
        val current = management() ?: return PeerActionOutcome.UNAVAILABLE
        return guarded { block(current) } ?: PeerActionOutcome.FAILED
    }

    /** Runs [block], rethrows cancellation and turns a trust failure into `null`. */
    private suspend fun <T> guarded(block: suspend () -> T): T? = try {
        block()
    } catch (cancelled: CancellationException) {
        throw cancelled
    } catch (_: TrustException) {
        null
    }
}

/** The running service's coordinator, attached by the debug `BuildSessionExtensions`. */
internal fun debugPeerActions(): DebugPeerActions = DebugPeerActions(
    management = { DebugRevocationRuntime.management() },
    status = { SharingState.trustStatus.value },
)

/** The single status label; [TrustStatus.RE_PAIR_REQUIRED] is the only fail-closed message. */
internal fun trustStatusLabel(status: TrustStatus): Int = when (status) {
    TrustStatus.OK -> R.string.pairing_debug_trust_ok
    TrustStatus.RE_PAIR_REQUIRED -> R.string.pairing_debug_trust_re_pair
}
