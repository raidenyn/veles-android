package me.nagaev.veles.session

/** §4 pre-auth plaintext framing: `type u8 || payload_len u16be || payload`, at most 4096 bytes. */
object PreAuthCodec {
    private const val HEADER_BYTES = 3
    private const val MAX_FRAME = 4096
    private const val U8_MASK = 0xFF
    private const val BITS_PER_BYTE = 8

    fun encode(type: Int, payload: ByteArray): ByteArray {
        if (FrameType.fromWire(type) == null) reject()
        if (payload.size > MAX_FRAME - HEADER_BYTES) reject()
        val frame = ByteArray(HEADER_BYTES + payload.size)
        frame[0] = type.toByte()
        frame[1] = (payload.size ushr BITS_PER_BYTE).toByte()
        frame[2] = payload.size.toByte()
        payload.copyInto(frame, HEADER_BYTES)
        return frame
    }

    fun decode(frame: ByteArray): Pair<Int, ByteArray> {
        if (frame.size < HEADER_BYTES || frame.size > MAX_FRAME) reject()
        val type = frame[0].toInt() and U8_MASK
        if (FrameType.fromWire(type) == null) reject()
        val payloadLength = ((frame[1].toInt() and U8_MASK) shl BITS_PER_BYTE) or (frame[2].toInt() and U8_MASK)
        if (payloadLength != frame.size - HEADER_BYTES) reject()
        return type to frame.copyOfRange(HEADER_BYTES, frame.size)
    }

    private fun reject(): Nothing = throw SessionReject()
}
