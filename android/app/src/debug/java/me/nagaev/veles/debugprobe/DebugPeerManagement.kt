package me.nagaev.veles.debugprobe

import me.nagaev.veles.sharing.RemovalResult
import me.nagaev.veles.trust.PeerRevocationCoordinator
import me.nagaev.veles.trust.PeerSummary

/**
 * The revocation-coordinator surface the debug controls drive (OTP-11 design D9). It exists so
 * the debug screen and the `REVOKE_PEER` entry point are written against the same four
 * operations the coordinator owns, and so the unit tests exercise the control logic without a
 * transport.
 */
internal interface PeerManagement {
    suspend fun peers(): List<PeerSummary>

    suspend fun revoke(peerId: String)

    suspend fun removeMutually(peerId: String): RemovalResult

    suspend fun reset()
}

/** The production adapter: every debug peer action is the coordinator's own operation. */
internal class CoordinatorPeerManagement(
    private val coordinator: PeerRevocationCoordinator,
) : PeerManagement {
    override suspend fun peers(): List<PeerSummary> = coordinator.peers()

    override suspend fun revoke(peerId: String) = coordinator.revoke(peerId)

    override suspend fun removeMutually(peerId: String): RemovalResult = coordinator.removeMutually(peerId)

    override suspend fun reset() = coordinator.reset()
}

/**
 * The debug build type's handle on the running coordinator. The production service attaches it
 * through `BuildSessionExtensions`; a release build links neither this object nor the debug
 * surfaces that read it. It is null while the sharing service is not running, and the debug
 * controls then report that they are unavailable instead of touching a coordinator that no
 * session can be closed through.
 */
internal object DebugRevocationRuntime {
    @Volatile
    private var coordinator: PeerRevocationCoordinator? = null

    fun attach(value: PeerRevocationCoordinator?) {
        coordinator = value
    }

    fun detach() {
        coordinator = null
    }

    fun management(): PeerManagement? = coordinator?.let(::CoordinatorPeerManagement)
}
