package me.nagaev.veles.pairing

/**
 * OTP2-ENC-010 pairing frame types (0x01-0x05).
 *
 * This is deliberately a separate enum from the reconnect
 * [me.nagaev.veles.session.FrameType]. The reconnect engine accepts and emits a
 * fixed pre-auth set (0x06, 0x07, 0x0F); adding pairing frames there would widen
 * it, so pairing has its own enum and its own allow-list.
 */
enum class PairingFrameType(val code: Int) {
    REQUEST(0x01),
    PA(0x02),
    PB(0x03),
    CA(0x04),
    CB(0x05),
    ;

    companion object {
        fun fromCode(value: Int): PairingFrameType? = entries.firstOrNull { it.code == value }
    }
}

/** One decoded pairing frame. */
class PairingFrame(val type: PairingFrameType, val payload: ByteArray)

/**
 * Malformed pairing frame. It carries no text so no caller can echo payload bytes
 * (a code, point or confirmation) into a diagnostic.
 */
class PairingFrameReject : Exception()

/**
 * `type u8 || payload_len u16be || payload`, the §4 framing shared with pre-auth
 * reconnect frames, at most 4096 bytes total (payload ≤ 4093).
 */
object PairingFrames {
    private const val HEADER_BYTES = 3
    private const val MAX_FRAME = 4096
    private const val U8_MASK = 0xFF
    private const val BITS_PER_BYTE = 8
    private const val HIGH_BYTE_SHIFT = 8
    private const val PREAUTH_REJECT = 0x0F

    /**
     * The constant empty-payload `preauth_reject` frame (OTP2-ENC-010e). Pairing
     * failures emit exactly these bytes, the same ones the reconnect engine sends,
     * so no reason code and no payload can cross the wire.
     */
    fun preauthReject(): ByteArray = byteArrayOf(PREAUTH_REJECT.toByte(), 0, 0)

    fun encode(type: PairingFrameType, payload: ByteArray): ByteArray {
        if (payload.size > MAX_FRAME - HEADER_BYTES) reject()
        val frame = ByteArray(HEADER_BYTES + payload.size)
        frame[0] = type.code.toByte()
        frame[1] = (payload.size ushr HIGH_BYTE_SHIFT).toByte()
        frame[2] = payload.size.toByte()
        payload.copyInto(frame, HEADER_BYTES)
        return frame
    }

    fun decode(frame: ByteArray): PairingFrame {
        if (frame.size < HEADER_BYTES || frame.size > MAX_FRAME) reject()
        val type = PairingFrameType.fromCode(frame[0].toInt() and U8_MASK) ?: reject()
        val payloadLength = ((frame[1].toInt() and U8_MASK) shl BITS_PER_BYTE) or (frame[2].toInt() and U8_MASK)
        if (payloadLength != frame.size - HEADER_BYTES) reject()
        return PairingFrame(type, frame.copyOfRange(HEADER_BYTES, frame.size))
    }

    private fun reject(): Nothing = throw PairingFrameReject()
}
