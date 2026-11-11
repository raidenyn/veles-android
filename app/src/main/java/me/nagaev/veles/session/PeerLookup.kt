package me.nagaev.veles.session

/** Local trust-record state that can answer a reconnect hello (veles-v1.md §11.3). */
enum class LocalRecordState { ACTIVE, PENDING, COMMITTED_WAITING_ACK }

/** The trust record a phone matches a client hello's installation id against. */
data class PeerRecord(
    val installationId: ByteArray,
    val identityPublicKey: ByteArray,
    val state: LocalRecordState,
)

/**
 * Trust lookup. The engine only reads; successful-authentication metadata (`lastSeen`) is
 * written by the host after the [SessionEffect.Authenticated] effect.
 */
fun interface PeerLookup {
    suspend fun find(installationId: ByteArray): PeerRecord?
}

/** Platform identity signer (Android Keystore DER `SHA256withECDSA` in production). */
fun interface IdentitySigner {
    suspend fun signDer(message: ByteArray): ByteArray
}
