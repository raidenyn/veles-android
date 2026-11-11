package me.nagaev.veles.debugprobe

import me.nagaev.veles.trust.HASH_BYTES
import me.nagaev.veles.trust.INSTALLATION_ID_BYTES
import me.nagaev.veles.trust.TrustEncoding
import me.nagaev.veles.trust.TrustException
import me.nagaev.veles.trust.TrustState
import me.nagaev.veles.trust.TrustStorage
import me.nagaev.veles.trust.TrustedPeer
import me.nagaev.veles.trust.checkTrust
import me.nagaev.veles.trust.peerFingerprint

private const val DEBUG_LABEL = "Veles debug peer"
private const val DESKTOP_DEVICE_TYPE = 1
private const val DEBUG_TRANSCRIPT = "ab"

/**
 * Debug-only trust injection (design D5). Writes one ordinary [TrustedPeer] in state
 * [TrustState.ACTIVE] through [TrustStorage.transaction], bypassing the activation proofs.
 * The record uses the production fingerprint and every production bound, so the unchanged
 * handshake authenticates against it like any enrolled peer.
 */
internal class DebugTrustInjector(
    private val storage: TrustStorage,
    private val elapsedMillis: () -> Long = System::currentTimeMillis,
    private val wallMillis: () -> Long = System::currentTimeMillis,
) {
    suspend fun injectActive(id: String, pub: String) {
        storage.transaction { stored ->
            val snapshot = stored ?: throw TrustException()
            TrustEncoding.requireHex(id, INSTALLATION_ID_BYTES)
            TrustEncoding.validatePublicKey(pub)
            checkTrust(id != snapshot.identity.installationId)
            val now = wallMillis()
            val peer = TrustedPeer(
                installationId = id,
                publicKey = pub,
                label = DEBUG_LABEL,
                deviceType = DESKTOP_DEVICE_TYPE,
                enrollmentTranscript = DEBUG_TRANSCRIPT.repeat(HASH_BYTES),
                fingerprint = peerFingerprint(snapshot.identity.publicKey, pub),
                state = TrustState.ACTIVE,
                createdAtMillis = now,
                lastSeenMillis = null,
                createdElapsedMillis = elapsedMillis(),
                bootToken = snapshot.continuityToken,
                stateChangedAtMillis = now,
            )
            // Replacing an existing record keeps debug injection idempotent.
            snapshot.copy(peers = snapshot.peers.filterNot { it.installationId == id } + peer) to Unit
        }
    }
}
