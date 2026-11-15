package me.nagaev.veles.trust

import androidx.annotation.VisibleForTesting
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.Serializable
import java.security.MessageDigest
import java.security.SecureRandom

internal const val INSTALLATION_ID_BYTES = 16
internal const val HASH_BYTES = 32
private const val PENDING_LIFETIME_MILLIS = 86_400_000L
private const val MAX_LABEL_BYTES = 32

open class TrustException : IllegalStateException("Trust unavailable; re-pairing required")

/** A failure to read, validate or persist the trust store; the user-facing text is unchanged. */
class TrustStoreException : TrustException()

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

    /** Removes the installation key; a missing key is not an error, so a repeat is safe. */
    fun delete()
}

interface TrustStorage {
    /** Read, validate and replace occur in one durable transaction; exceptions roll back. */
    suspend fun <T> transaction(block: (TrustSnapshot?) -> Pair<TrustSnapshot, T>): T

    /** Deletes the stored trust without reading or decoding it; a repeat is safe. */
    suspend fun reset()
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

    /**
     * Fences [peerId] against [addPending] until the matching [endRevocation]; reference-counted
     * per id and in memory only. While fenced, [addPending] for the id is a peer rejection.
     */
    suspend fun beginRevocation(peerId: String)

    /** Releases one [beginRevocation] for [peerId]; the fence lifts only after the last one. */
    suspend fun endRevocation(peerId: String)

    /**
     * Records a successful authenticated reconnect (veles-v1.md §11.3). Applies to any existing,
     * validated record that can authenticate — `ACTIVE`, `PENDING` or `COMMITTED_WAITING_ACK` —
     * and never changes the state, its transition time or a pending record's lifetime. An
     * unknown, expired or deleted id throws [TrustException].
     */
    suspend fun markAuthenticated(peerId: String)

    /**
     * The explicit, user-initiated identity reset: deletes the stored trust without decoding it,
     * then deletes the installation key. It is idempotent, leaves the store fail-closed if it is
     * interrupted after either deletion, and is never invoked by a failure path.
     */
    suspend fun resetIdentity()
}

@Suppress("TooManyFunctions") // Implements the whole InstallationTrustRepository surface.
class TrustRepository(
    private val storage: TrustStorage,
    private val keys: IdentityKeyStore,
    private val elapsedMillis: () -> Long,
    private val bootToken: () -> String,
    private val wallMillis: () -> Long,
    private val activationVerifier: ActivationVerifier,
    private val onStoreFailure: () -> Unit = {},
) : InstallationTrustRepository {
    private val fenceLock = Any()
    private val fencedPeers = mutableMapOf<String, Int>()
    private val storeLock = Mutex()
    private val episodeLock = Any()
    private var storeFailureReported = false

    override suspend fun identity(): InstallationIdentity = update { it to it.identity }
    override suspend fun peers(): List<TrustedPeer> = update { it to it.peers.toList() }
    override suspend fun activePeers(): List<TrustedPeer> = peers().filter { it.state == TrustState.ACTIVE }

    override suspend fun beginRevocation(peerId: String) {
        synchronized(fenceLock) { fencedPeers[peerId] = (fencedPeers[peerId] ?: 0) + 1 }
    }

    override suspend fun endRevocation(peerId: String) {
        synchronized(fenceLock) {
            val remaining = (fencedPeers[peerId] ?: return) - 1
            if (remaining <= 0) fencedPeers.remove(peerId) else fencedPeers[peerId] = remaining
        }
    }

    private fun isRevocationFenced(peerId: String): Boolean = synchronized(fenceLock) { fencedPeers.containsKey(peerId) }

    override suspend fun addPending(peer: PendingPeer) = update { snapshot ->
        checkTrust(!isRevocationFenced(peer.installationId))
        checkTrust(snapshot.peers.none { it.installationId == peer.installationId })
        val createdAt = wallMillis()
        // The fingerprint is computed from caller-supplied hex, so a malformed key is a store
        // failure here and not a peer rejection.
        val fingerprint = storeValue { peerFingerprint(snapshot.identity.publicKey, peer.publicKey) }
        val record = TrustedPeer(
            peer.installationId, peer.publicKey, peer.label, peer.deviceType, peer.enrollmentTranscript,
            fingerprint, TrustState.PENDING,
            createdAt, null, elapsedMillis(), snapshot.continuityToken, createdAt,
        )
        // The resulting snapshot is validated after the operation, so a bad record is a store failure.
        snapshot.copy(peers = snapshot.peers + record) to Unit
    }

    override suspend fun confirm(peerId: String, step: ActivationStep, proof: ByteArray) = update { snapshot ->
        val peer = snapshot.peers.singleOrNull { it.installationId == peerId } ?: throw TrustException()
        val expected = if (step == ActivationStep.COMMIT) TrustState.PENDING else TrustState.COMMITTED_WAITING_ACK
        val next = if (step == ActivationStep.COMMIT) TrustState.COMMITTED_WAITING_ACK else TrustState.ACTIVE
        // A duplicate COMMIT on an ACTIVE record is a no-op, but only once the stage MAC verifies
        // (veles-v1.md §11.4, OTP2-STA-010b); the same holds for a duplicate ACK.
        val duplicate = peer.state == TrustState.ACTIVE
        checkTrust((peer.state == expected || peer.state == next || duplicate) && activationVerifier.verify(peer, step, proof.copyOf()))
        snapshot.copy(
            peers = snapshot.peers.map {
                // Only the accepted state this step moves from transitions, so no state can regress.
                if (it == peer && peer.state == expected) it.copy(state = next, stateChangedAtMillis = wallMillis()) else it
            },
        ) to Unit
    }

    override suspend fun revoke(peerId: String) = update { snapshot ->
        snapshot.copy(peers = snapshot.peers.filterNot { it.installationId == peerId }) to Unit
    }

    /**
     * The write is metadata only: one transaction, `lastSeenMillis` replaced, every state and
     * pending-lifetime field kept byte-identical, so a pending record expires on its original
     * deadline (veles-v1.md §11.3).
     */
    override suspend fun markAuthenticated(peerId: String) = update { snapshot ->
        val peer = snapshot.peers.singleOrNull { it.installationId == peerId } ?: throw TrustException()
        val now = wallMillis()
        snapshot.copy(peers = snapshot.peers.map { if (it == peer) it.copy(lastSeenMillis = now) else it }) to Unit
    }

    /**
     * The only path that discards trust and the installation key, and it decodes nothing, so it
     * works on an undecodable payload. The trust row goes first, then the key: an interruption
     * after either deletion still fails closed, and a repeated call completes it. It shares
     * [storeLock] with transactions so no use can recreate a snapshot between the two deletions,
     * and a completed reset ends the failure episode.
     */
    override suspend fun resetIdentity() {
        storeLock.withLock {
            try {
                storage.reset()
                keys.delete()
                endStoreFailureEpisode()
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (_: Exception) {
                // No reset step is a peer-level check, and a reset failure never opens a store
                // failure episode: only an explicit user action reaches this path.
                throw TrustStoreException()
            }
        }
    }

    /**
     * One transaction and its failure-episode transition share the same critical section, so a
     * success can only clear the episode, or a failure start one, in transaction order.
     */
    private suspend fun <T> update(block: (TrustSnapshot) -> Pair<TrustSnapshot, T>): T {
        var report = false
        try {
            val result = storeLock.withLock {
                when (
                    val outcome = storeTransaction { stored ->
                        val snapshot = stored ?: run {
                            // A surviving key without its installation metadata is not a fresh installation.
                            storeTrust(readIdentityKey(keys) == null)
                            TrustSnapshot(
                                InstallationIdentity(
                                    TrustEncoding.hex(ByteArray(INSTALLATION_ID_BYTES).also(SecureRandom()::nextBytes)),
                                    generateIdentityKey(keys),
                                ),
                            )
                        }
                        validateSnapshot(snapshot, stored != null)
                        storeTrust(snapshot.identity.publicKey == readIdentityKey(keys))
                        val now = elapsedMillis()
                        val boot = bootToken()
                        storeTrust(now >= 0 && boot.isNotEmpty())
                        val token = if (snapshot.bootMarker == boot) {
                            storeCheck { TrustEncoding.requireHex(snapshot.continuityToken, HASH_BYTES) }
                            snapshot.continuityToken
                        } else {
                            TrustEncoding.hex(ByteArray(HASH_BYTES).also(SecureRandom()::nextBytes))
                        }
                        val (next, value) = block(
                            snapshot.copy(
                                bootMarker = boot,
                                continuityToken = token,
                                peers = snapshot.peers.filter {
                                    it.state == TrustState.ACTIVE ||
                                        (
                                            it.bootToken == token &&
                                                now >= it.createdElapsedMillis &&
                                                now - it.createdElapsedMillis < PENDING_LIFETIME_MILLIS
                                            )
                                },
                            ),
                        )
                        // Failures after the operation returned — validating its result and the durable
                        // write in [TrustStorage.transaction] — are store failures too.
                        validateSnapshot(next, stored = false)
                        next to value
                    }
                ) {
                    is StoreTransaction.Committed -> {
                        endStoreFailureEpisode()
                        outcome.value
                    }
                    is StoreTransaction.Failed -> {
                        report = outcome.failure is TrustStoreException && beginStoreFailureEpisode()
                        throw outcome.failure
                    }
                }
            }
            return result
        } finally {
            if (report) onStoreFailure()
        }
    }

    /**
     * Runs one storage transaction and classifies its failure, without throwing. Any failure that
     * is not an explicit peer rejection is normalised to [TrustStoreException]; cancellation is
     * returned untouched so it is never reported.
     */
    private suspend fun <T> storeTransaction(
        block: (TrustSnapshot?) -> Pair<TrustSnapshot, T>,
    ): StoreTransaction<T> = try {
        StoreTransaction.Committed(storage.transaction(block))
    } catch (cancelled: CancellationException) {
        StoreTransaction.Failed(cancelled)
    } catch (failure: TrustException) {
        // A peer-level rejection keeps its own classification.
        StoreTransaction.Failed(failure)
    } catch (_: Exception) {
        StoreTransaction.Failed(TrustStoreException())
    }

    /** Ends the current store-failure episode; a later store failure reports again. */
    @VisibleForTesting
    internal fun endStoreFailureEpisode() {
        synchronized(episodeLock) { storeFailureReported = false }
    }

    /** Starts an episode, returning true only for the failure that opened it. */
    private fun beginStoreFailureEpisode(): Boolean = synchronized(episodeLock) {
        if (storeFailureReported) {
            false
        } else {
            storeFailureReported = true
            true
        }
    }
}

private sealed interface StoreTransaction<out T> {
    class Committed<T>(val value: T) : StoreTransaction<T>

    class Failed(val failure: Exception) : StoreTransaction<Nothing>
}

private fun readIdentityKey(keys: IdentityKeyStore): String? = try {
    keys.publicKey()
} catch (cancelled: CancellationException) {
    throw cancelled
} catch (_: Exception) {
    throw TrustStoreException()
}

private fun generateIdentityKey(keys: IdentityKeyStore): String = try {
    keys.generate()
} catch (cancelled: CancellationException) {
    throw cancelled
} catch (_: Exception) {
    throw TrustStoreException()
}

private fun storeTrust(value: Boolean) {
    if (!value) throw TrustStoreException()
}

/** Normalizes any trust-validation failure into a store failure. */
private inline fun <T> storeValue(block: () -> T): T = try {
    block()
} catch (_: TrustException) {
    throw TrustStoreException()
}

private inline fun storeCheck(block: () -> Unit) {
    storeValue(block)
}

private fun validateSnapshot(snapshot: TrustSnapshot, stored: Boolean) {
    storeCheck {
        if (stored) {
            checkTrust(snapshot.bootMarker.isNotEmpty())
            TrustEncoding.requireHex(snapshot.continuityToken, HASH_BYTES)
        }
        TrustEncoding.requireHex(snapshot.identity.installationId, INSTALLATION_ID_BYTES)
        TrustEncoding.validatePublicKey(snapshot.identity.publicKey)
        checkTrust(snapshot.peers.map { it.installationId }.distinct().size == snapshot.peers.size)
    }
    snapshot.peers.forEach { validatePeer(it, snapshot.identity) }
}

/** Per-peer validation is a store failure, whether it runs before or after the operation. */
private fun validatePeer(peer: TrustedPeer, identity: InstallationIdentity) {
    storeCheck {
        TrustEncoding.requireHex(peer.installationId, INSTALLATION_ID_BYTES)
        TrustEncoding.requireHex(peer.enrollmentTranscript, HASH_BYTES)
        TrustEncoding.validatePublicKey(peer.publicKey)
        checkTrust(peer.installationId != identity.installationId)
        checkTrust(peer.deviceType in 1..2 && peer.label.toByteArray(Charsets.UTF_8).size <= MAX_LABEL_BYTES)
        checkTrust(peer.createdAtMillis >= 0 && (peer.lastSeenMillis == null || peer.lastSeenMillis >= 0))
        checkTrust(peer.stateChangedAtMillis >= 0 && peer.createdElapsedMillis >= 0)
        TrustEncoding.requireHex(peer.bootToken, HASH_BYTES)
        checkTrust(peer.fingerprint == peerFingerprint(identity.publicKey, peer.publicKey))
    }
}

/** The OTP2-REC-011a identity fingerprint; shared with the debug injector so records validate. */
internal fun peerFingerprint(phone: String, client: String): String = TrustEncoding.hex(
    MessageDigest.getInstance("SHA-256").digest(
        "Veles identity fingerprint v1".toByteArray(Charsets.US_ASCII) +
            TrustEncoding.bytes(phone) + TrustEncoding.bytes(client),
    ),
)

internal fun checkTrust(value: Boolean) {
    if (!value) throw TrustException()
}
