package me.nagaev.veles.trust

import kotlinx.serialization.Serializable
import java.security.MessageDigest
import java.security.SecureRandom

internal const val INSTALLATION_ID_BYTES = 16
internal const val HASH_BYTES = 32
private const val PENDING_LIFETIME_MILLIS = 86_400_000L
private const val MAX_LABEL_BYTES = 32

class TrustException : IllegalStateException("Trust unavailable; re-pairing required")

@Serializable
data class InstallationIdentity(val installationId: String, val publicKey: String)

@Serializable
enum class TrustState { PENDING, COMMITTED_WAITING_ACK, ACTIVE }
enum class ActivationStep { COMMIT, ACK }

data class PendingPeer(
    val installationId: String,
    val publicKey: String,
    val label: String,
    val deviceType: Int,
    val enrollmentTranscript: String,
)

@Serializable
data class TrustedPeer(
    val installationId: String,
    val publicKey: String,
    val label: String,
    val deviceType: Int,
    val enrollmentTranscript: String,
    val fingerprint: String,
    val state: TrustState,
    val createdAtMillis: Long,
    val lastSeenMillis: Long?,
    val createdElapsedMillis: Long,
    val bootToken: String,
    val stateChangedAtMillis: Long,
)

@Serializable
data class TrustSnapshot(
    val identity: InstallationIdentity,
    val peers: List<TrustedPeer> = emptyList(),
    val bootMarker: String = "",
    val continuityToken: String = "",
)

interface IdentityKeyStore {
    fun publicKey(): String?
    fun generate(): String
    fun sign(message: ByteArray): ByteArray
}

interface TrustStorage {
    /** Read, validate and replace occur in one durable transaction; exceptions roll back. */
    suspend fun <T> transaction(block: (TrustSnapshot?) -> Pair<TrustSnapshot, T>): T
}

/** Supplied by the authenticated protocol session, never by a UI or a persisted flag.
 * Must validate the stage-specific MAC, transcript and current session binding.
 */
fun interface ActivationVerifier {
    fun verify(peer: TrustedPeer, step: ActivationStep, proof: ByteArray): Boolean
}

interface InstallationTrustRepository {
    suspend fun identity(): InstallationIdentity
    suspend fun peers(): List<TrustedPeer>
    suspend fun activePeers(): List<TrustedPeer>
    suspend fun addPending(peer: PendingPeer)
    suspend fun confirm(peerId: String, step: ActivationStep, proof: ByteArray)
    suspend fun revoke(peerId: String)
}

class TrustRepository(
    private val storage: TrustStorage,
    private val keys: IdentityKeyStore,
    private val elapsedMillis: () -> Long,
    private val bootToken: () -> String,
    private val wallMillis: () -> Long,
    private val activationVerifier: ActivationVerifier,
) : InstallationTrustRepository {
    override suspend fun identity(): InstallationIdentity = update { it to it.identity }
    override suspend fun peers(): List<TrustedPeer> = update { it to it.peers.toList() }
    override suspend fun activePeers(): List<TrustedPeer> = peers().filter { it.state == TrustState.ACTIVE }

    override suspend fun addPending(peer: PendingPeer) = update { snapshot ->
        checkTrust(snapshot.peers.none { it.installationId == peer.installationId })
        val createdAt = wallMillis()
        val record = TrustedPeer(
            peer.installationId, peer.publicKey, peer.label, peer.deviceType, peer.enrollmentTranscript,
            fingerprint(snapshot.identity.publicKey, peer.publicKey), TrustState.PENDING,
            createdAt, null, elapsedMillis(), snapshot.continuityToken, createdAt,
        )
        validatePeer(record, snapshot.identity)
        snapshot.copy(peers = snapshot.peers + record) to Unit
    }

    override suspend fun confirm(peerId: String, step: ActivationStep, proof: ByteArray) = update { snapshot ->
        val peer = snapshot.peers.singleOrNull { it.installationId == peerId } ?: throw TrustException()
        val expected = if (step == ActivationStep.COMMIT) TrustState.PENDING else TrustState.COMMITTED_WAITING_ACK
        val next = if (step == ActivationStep.COMMIT) TrustState.COMMITTED_WAITING_ACK else TrustState.ACTIVE
        checkTrust((peer.state == expected || peer.state == next) && activationVerifier.verify(peer, step, proof.copyOf()))
        snapshot.copy(peers = snapshot.peers.map {
            if (it == peer && peer.state != next) it.copy(state = next, stateChangedAtMillis = wallMillis()) else it
        }) to Unit
    }

    override suspend fun revoke(peerId: String) = update { snapshot ->
        snapshot.copy(peers = snapshot.peers.filterNot { it.installationId == peerId }) to Unit
    }

    private suspend fun <T> update(block: (TrustSnapshot) -> Pair<TrustSnapshot, T>): T =
        storage.transaction { stored ->
            val snapshot = stored ?: run {
                // A surviving key without its installation metadata is not a fresh installation.
                checkTrust(keys.publicKey() == null)
                TrustSnapshot(InstallationIdentity(
                    TrustEncoding.hex(ByteArray(INSTALLATION_ID_BYTES).also(SecureRandom()::nextBytes)), keys.generate(),
                ))
            }
            checkTrust(snapshot.identity.publicKey == keys.publicKey())
            if (stored != null) {
                checkTrust(snapshot.bootMarker.isNotEmpty())
                TrustEncoding.requireHex(snapshot.continuityToken, HASH_BYTES)
            }
            TrustEncoding.requireHex(snapshot.identity.installationId, INSTALLATION_ID_BYTES)
            TrustEncoding.validatePublicKey(snapshot.identity.publicKey)
            checkTrust(snapshot.peers.map { it.installationId }.distinct().size == snapshot.peers.size)
            snapshot.peers.forEach { validatePeer(it, snapshot.identity) }
            val now = elapsedMillis()
            val boot = bootToken()
            checkTrust(now >= 0 && boot.isNotEmpty())
            val token = if (snapshot.bootMarker == boot) {
                TrustEncoding.requireHex(snapshot.continuityToken, HASH_BYTES)
                snapshot.continuityToken
            } else {
                TrustEncoding.hex(ByteArray(HASH_BYTES).also(SecureRandom()::nextBytes))
            }
            block(snapshot.copy(bootMarker = boot, continuityToken = token, peers = snapshot.peers.filter {
                it.state == TrustState.ACTIVE ||
                    (it.bootToken == token && now >= it.createdElapsedMillis &&
                        now - it.createdElapsedMillis < PENDING_LIFETIME_MILLIS)
            }))
        }

    private fun validatePeer(peer: TrustedPeer, identity: InstallationIdentity) {
        TrustEncoding.requireHex(peer.installationId, INSTALLATION_ID_BYTES)
        TrustEncoding.requireHex(peer.enrollmentTranscript, HASH_BYTES)
        TrustEncoding.validatePublicKey(peer.publicKey)
        checkTrust(peer.installationId != identity.installationId)
        checkTrust(peer.deviceType in 1..2 && peer.label.toByteArray(Charsets.UTF_8).size <= MAX_LABEL_BYTES)
        checkTrust(peer.createdAtMillis >= 0 && (peer.lastSeenMillis == null || peer.lastSeenMillis >= 0))
        checkTrust(peer.stateChangedAtMillis >= 0 && peer.createdElapsedMillis >= 0)
        TrustEncoding.requireHex(peer.bootToken, HASH_BYTES)
        checkTrust(peer.fingerprint == fingerprint(identity.publicKey, peer.publicKey))
    }

    private fun fingerprint(phone: String, client: String): String = TrustEncoding.hex(
        MessageDigest.getInstance("SHA-256").digest(
            "Veles identity fingerprint v1".toByteArray(Charsets.US_ASCII) +
                TrustEncoding.bytes(phone) + TrustEncoding.bytes(client),
        ),
    )
}

internal fun checkTrust(value: Boolean) { if (!value) throw TrustException() }
