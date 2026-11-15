package me.nagaev.veles.trust

/**
 * One read-only peer listing entry (design D5). It is computed fresh from the repository plus
 * the session registry on every call and deliberately carries no key material, no platform hint
 * and no native handle: the fingerprint is the only identity digest a surface may show.
 */
data class PeerSummary(
    val installationId: String,
    val label: String,
    val fingerprint: String,
    val deviceType: Int,
    val state: TrustState,
    val createdAtMillis: Long,
    val lastSeenMillis: Long?,
    val connected: Boolean,
)

/**
 * The only trust status a surface may show (design D6). [RE_PAIR_REQUIRED] is the single
 * fail-closed message, and it never suggests erasing trust automatically.
 */
enum class TrustStatus { OK, RE_PAIR_REQUIRED }
