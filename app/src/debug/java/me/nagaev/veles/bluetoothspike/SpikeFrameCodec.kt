package me.nagaev.veles.bluetoothspike

internal class SpikeFrameException(message: String) : IllegalArgumentException(message)

internal data class SpikeFrame(
    val messageId: Int,
    val chunkIndex: Int,
    val chunkCount: Int,
    val payload: ByteArray,
)

internal object SpikeFrameCodec {
    fun split(messageId: Int, payload: ByteArray): List<ByteArray> {
        if (messageId < 0 || messageId > 0xFFFF) {
            throw SpikeFrameException("Message ID out of range: $messageId")
        }
        if (payload.isEmpty()) {
            throw SpikeFrameException("Payload is empty")
        }
        if (payload.size > SpikeProtocol.MAX_MESSAGE_BYTES) {
            throw SpikeFrameException("Payload too large: ${payload.size} bytes")
        }

        val chunkCount = (payload.size + SpikeProtocol.FRAME_PAYLOAD_BYTES - 1) / SpikeProtocol.FRAME_PAYLOAD_BYTES
        if (chunkCount > SpikeProtocol.MAX_CHUNKS) {
            throw SpikeFrameException("Too many chunks: $chunkCount")
        }

        val frames = mutableListOf<ByteArray>()
        var offset = 0
        for (chunkIndex in 0 until chunkCount) {
            val end = minOf(offset + SpikeProtocol.FRAME_PAYLOAD_BYTES, payload.size)
            val chunkPayloadLength = end - offset
            val frame = ByteArray(SpikeProtocol.FRAME_HEADER_BYTES + chunkPayloadLength)
            frame[0] = SpikeProtocol.FRAME_VERSION.toByte()
            frame[1] = ((messageId shr 8) and 0xFF).toByte()
            frame[2] = (messageId and 0xFF).toByte()
            frame[3] = chunkIndex.toByte()
            frame[4] = chunkCount.toByte()
            frame[5] = chunkPayloadLength.toByte()
            System.arraycopy(payload, offset, frame, SpikeProtocol.FRAME_HEADER_BYTES, chunkPayloadLength)
            frames.add(frame)
            offset = end
        }
        return frames
    }

    fun decode(bytes: ByteArray): SpikeFrame {
        if (bytes.size < SpikeProtocol.FRAME_HEADER_BYTES) {
            throw SpikeFrameException("Frame too short: ${bytes.size} bytes")
        }
        if (bytes.size > SpikeProtocol.MAX_FRAME_BYTES) {
            throw SpikeFrameException("Frame too long: ${bytes.size} bytes")
        }
        val version = bytes[0].toInt() and 0xFF
        if (version != SpikeProtocol.FRAME_VERSION) {
            throw SpikeFrameException("Unsupported frame version: $version")
        }
        val messageId = ((bytes[1].toInt() and 0xFF) shl 8) or (bytes[2].toInt() and 0xFF)
        val chunkIndex = bytes[3].toInt() and 0xFF
        val chunkCount = bytes[4].toInt() and 0xFF
        val payloadLength = bytes[5].toInt() and 0xFF
        if (chunkCount == 0 || chunkCount > SpikeProtocol.MAX_CHUNKS) {
            throw SpikeFrameException("Invalid chunk count: $chunkCount")
        }
        if (chunkIndex >= chunkCount) {
            throw SpikeFrameException("Invalid chunk index: $chunkIndex for count $chunkCount")
        }
        if (payloadLength == 0) {
            throw SpikeFrameException("Empty frame payload")
        }
        if (payloadLength > SpikeProtocol.FRAME_PAYLOAD_BYTES) {
            throw SpikeFrameException("Payload length exceeds chunk size: $payloadLength")
        }
        if (bytes.size != SpikeProtocol.FRAME_HEADER_BYTES + payloadLength) {
            throw SpikeFrameException("Payload length mismatch: declared $payloadLength, actual ${bytes.size - SpikeProtocol.FRAME_HEADER_BYTES}")
        }
        val payload = bytes.copyOfRange(SpikeProtocol.FRAME_HEADER_BYTES, SpikeProtocol.FRAME_HEADER_BYTES + payloadLength)
        return SpikeFrame(messageId, chunkIndex, chunkCount, payload)
    }
}

private class PendingMessage(
    val chunkCount: Int,
    val createdAtMillis: Long,
    val chunks: Array<ByteArray?>,
)

internal class SpikeFrameReassembler(
    private val clockMillis: () -> Long,
) {
    private val messages = mutableMapOf<Pair<String, Int>, PendingMessage>()

    @Synchronized
    fun accept(clientId: String, frameBytes: ByteArray): ByteArray? {
        val frame = SpikeFrameCodec.decode(frameBytes)
        val key = clientId to frame.messageId
        val existing = messages[key]
        if (existing == null && frame.chunkIndex != 0) {
            return null
        }
        val pending = messages.getOrPut(key) {
            PendingMessage(
                chunkCount = frame.chunkCount,
                createdAtMillis = clockMillis(),
                chunks = arrayOfNulls(frame.chunkCount),
            )
        }
        if (pending.chunkCount != frame.chunkCount) {
            throw SpikeFrameException("Chunk count changed: was ${pending.chunkCount}, now ${frame.chunkCount}")
        }
        if (pending.chunks[frame.chunkIndex] != null) {
            throw SpikeFrameException("Duplicate chunk: ${frame.chunkIndex}")
        }
        pending.chunks[frame.chunkIndex] = frame.payload
        if (pending.chunks.any { it == null }) return null
        messages.remove(key)
        return pending.chunks.filterNotNull().fold(ByteArray(0)) { result, chunk -> result + chunk }
    }

    @Synchronized
    fun expire(): Int {
        val now = clockMillis()
        val expiredKeys = messages.entries
            .filter { now - it.value.createdAtMillis > SpikeProtocol.REASSEMBLY_TIMEOUT_MILLIS }
            .map { it.key }
        expiredKeys.forEach { messages.remove(it) }
        return expiredKeys.size
    }

    @Synchronized
    fun clearClient(clientId: String) {
        messages.keys.filter { it.first == clientId }.forEach { messages.remove(it) }
    }

    @Synchronized
    fun clear() {
        messages.clear()
    }
}
