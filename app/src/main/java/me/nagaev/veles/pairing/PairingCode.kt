package me.nagaev.veles.pairing

import java.security.SecureRandom

/**
 * A six-digit pairing code held in memory only. The digits are drawn uniformly by
 * rejection sampling, never leave this object except as the six ASCII bytes the
 * core consumes, and are erased when the window closes.
 *
 * [toString] deliberately redacts the digits so no log line, exception message or
 * debug dump can reveal them.
 */
class PairingCode internal constructor(digits: ByteArray) {
    private val digits = digits.copyOf()

    init {
        require(this.digits.size == DIGITS) { "pairing code must be six digits" }
        require(this.digits.all { it in ZERO..NINE }) { "pairing code must be six ASCII digits" }
    }

    internal fun copyBytes(): ByteArray = digits.copyOf()

    internal fun erase() {
        digits.fill(0)
    }

    override fun toString(): String = "PairingCode(REDACTED)"

    companion object {
        private const val DIGITS = 6
        private const val DECIMAL = 10L
        private const val BOUND = 1_000_000L
        private const val DRAW_BYTES = 4
        private const val DRAW_BITS = 32
        private const val BYTE_MASK = 0xFFL
        private const val SHIFT_24 = 24
        private const val SHIFT_16 = 16
        private const val SHIFT_8 = 8
        private const val LOW_BYTE_INDEX = 3
        private val ZERO: Byte = '0'.code.toByte()
        private val NINE: Byte = '9'.code.toByte()

        /**
         * Largest multiple of [BOUND] that fits in [DRAW_BITS] bits. Draws at or
         * above it fall in an incomplete bucket and are discarded, so every value
         * in `000000`-`999999` is equally likely (no modulo bias).
         */
        private val DRAW_LIMIT = (1L shl DRAW_BITS) - ((1L shl DRAW_BITS) % BOUND)

        internal fun draw(random: SecureRandom): PairingCode {
            val draw = ByteArray(DRAW_BYTES)
            try {
                while (true) {
                    random.nextBytes(draw)
                    val value = ((draw[0].toLong() and BYTE_MASK) shl SHIFT_24) or
                        ((draw[1].toLong() and BYTE_MASK) shl SHIFT_16) or
                        ((draw[2].toLong() and BYTE_MASK) shl SHIFT_8) or
                        (draw[LOW_BYTE_INDEX].toLong() and BYTE_MASK)
                    if (value >= DRAW_LIMIT) continue
                    return PairingCode(format(value % BOUND))
                }
            } finally {
                draw.fill(0)
            }
        }

        private fun format(value: Long): ByteArray {
            val digits = ByteArray(DIGITS)
            var remaining = value
            for (index in DIGITS - 1 downTo 0) {
                digits[index] = (ZERO + (remaining % DECIMAL)).toByte()
                remaining /= DECIMAL
            }
            return digits
        }
    }
}
