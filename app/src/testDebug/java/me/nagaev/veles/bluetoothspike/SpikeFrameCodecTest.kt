package me.nagaev.veles.bluetoothspike

import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNull

class SpikeFrameCodecTest {
    @Test
    fun `split uses six byte header and payloads no larger than fourteen bytes`() {
        val payload = ByteArray(29) { it.toByte() }
        val frames = SpikeFrameCodec.split(messageId = 0x1234, payload = payload)

        assertEquals(3, frames.size)
        assertContentEquals(byteArrayOf(1, 0x12, 0x34, 0, 3, 14), frames[0].copyOfRange(0, 6))
        assertContentEquals(byteArrayOf(1, 0x12, 0x34, 2, 3, 1), frames[2].copyOfRange(0, 6))
        frames.forEach { frame -> require(frame.size <= SpikeProtocol.MAX_FRAME_BYTES) }
    }

    @Test
    fun `reassembler isolates clients and returns complete payload only once`() {
        val now = longArrayOf(1_000L)
        val reassembler = SpikeFrameReassembler(clockMillis = { now[0] })
        val frames = SpikeFrameCodec.split(7, "authenticated payload".encodeToByteArray())

        assertNull(reassembler.accept("client-a", frames[0]))
        assertNull(reassembler.accept("client-b", frames[0]))
        val resultA = frames.drop(1).fold<ByteArray, ByteArray?>(null) { _, frame ->
            reassembler.accept("client-a", frame)
        }
        val resultB = frames.drop(1).fold<ByteArray, ByteArray?>(null) { _, frame ->
            reassembler.accept("client-b", frame)
        }

        assertContentEquals("authenticated payload".encodeToByteArray(), resultA)
        assertContentEquals("authenticated payload".encodeToByteArray(), resultB)
    }

    @Test
    fun `duplicate frame is rejected`() {
        val reassembler = SpikeFrameReassembler(clockMillis = { 1_000L })
        val frame = SpikeFrameCodec.split(9, ByteArray(20))[0]
        reassembler.accept("client", frame)

        assertFailsWith<SpikeFrameException> { reassembler.accept("client", frame) }
    }

    @Test
    fun `incomplete message expires after five seconds`() {
        var now = 1_000L
        val reassembler = SpikeFrameReassembler(clockMillis = { now })
        val frames = SpikeFrameCodec.split(11, ByteArray(20))
        reassembler.accept("client", frames[0])
        now += SpikeProtocol.REASSEMBLY_TIMEOUT_MILLIS + 1

        assertEquals(1, reassembler.expire())
        assertNull(reassembler.accept("client", frames[1]))
    }

    @Test
    fun `oversized payload and malformed header are rejected`() {
        assertFailsWith<SpikeFrameException> { SpikeFrameCodec.split(1, ByteArray(449)) }
        val wrongVersion = SpikeFrameCodec.split(1, byteArrayOf(7)).single().also { it[0] = 2 }
        assertFailsWith<SpikeFrameException> { SpikeFrameCodec.decode(wrongVersion) }
        val wrongLength = SpikeFrameCodec.split(1, byteArrayOf(7)).single().also { it[5] = 2 }
        assertFailsWith<SpikeFrameException> { SpikeFrameCodec.decode(wrongLength) }
    }

    @Test
    fun `wire message survives strict JSON round trip`() {
        val message = SpikeWireMessage(
            type = SpikeProtocol.TYPE_OTP,
            delivery = SpikeProtocol.DELIVERY_PUSH,
            eventId = 42,
            code = "654321",
            merchant = "Synthetic Shop",
            amount = "10.00",
            currency = "USD",
            phoneLabel = "Pixel-1234",
        )

        assertEquals(message, SpikeProtocol.decode(SpikeProtocol.encode(message)))
    }
}
