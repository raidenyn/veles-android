@file:Suppress("MagicNumber") // Fixed, public synthetic transport fixture; no OTP dependency.

package me.nagaev.veles.sharing

/** Debug-only caller: deterministic public bytes for physical transport boundary/isolation tests. */
internal object SyntheticTransportFixture {
    fun reply(record: ByteArray): ByteArray? {
        if (record.size !in 4..4096 || !record.copyOfRange(0, 4).contentEquals(byteArrayOf(0x56, 0x45, 0x4c, 1))) return null
        if (record.size > 4) {
            val seed = ((record[4].toInt() and 0xff) - 4) and 0xff
            if ((4 until record.size).any { record[it] != ((it + seed) and 0xff).toByte() }) return null
        }
        return record.copyOf().apply { this[3] = 2 }
    }
}
