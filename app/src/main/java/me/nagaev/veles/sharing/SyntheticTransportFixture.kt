@file:Suppress("MagicNumber") // Fixed, public synthetic transport fixture; no OTP dependency.

package me.nagaev.veles.sharing

import java.security.MessageDigest
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Debug-only caller: deterministic public bytes for physical transport boundary/isolation tests.
 * Every use sits behind `BuildConfig.DEBUG`, so R8 removes the whole object from release builds.
 */
internal object SyntheticTransportFixture {
    /** Hash-only record of one opt-in unsolicited push; carries no payload bytes and no raw address. */
    data class Unsolicited(
        val peer: String,
        val subscribedAtMillis: Long,
        val firedAtMillis: Long,
        val delayMillis: Long,
        val queued: Boolean,
        val bytes: Int,
        val sha256: String,
    )

    /**
     * Opt-in, set only by the physical instrumentation: wait this long after a central subscribes,
     * then push one [unsolicited] record to it without any request. Null (the default) disables it.
     */
    @Volatile var unsolicitedAfterMillis: Long? = null
    val unsolicitedEvidence = CopyOnWriteArrayList<Unsolicited>()

    fun reply(record: ByteArray): ByteArray? {
        if (record.size !in 4..4096 || !record.copyOfRange(0, 4).contentEquals(byteArrayOf(0x56, 0x45, 0x4c, 1))) return null
        if (record.size > 4) {
            val seed = ((record[4].toInt() and 0xff) - 4) and 0xff
            if ((4 until record.size).any { record[it] != ((it + seed) and 0xff).toByte() }) return null
        }
        return record.copyOf().apply { this[3] = 2 }
    }

    /** Record type 3: `VEL\x03`, then `(index + seed) & 0xff`. A desktop driver recomputes it independently. */
    fun unsolicited(length: Int = 4096, seed: Int = UNSOLICITED_SEED): ByteArray {
        require(length in 4..4096)
        return ByteArray(length) { ((it + seed) and 0xff).toByte() }.also {
            byteArrayOf(0x56, 0x45, 0x4c, 3).copyInto(it)
        }
    }

    fun sha256Hex(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

    const val UNSOLICITED_SEED = 61
}
