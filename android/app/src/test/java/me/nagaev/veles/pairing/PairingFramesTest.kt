package me.nagaev.veles.pairing

import me.nagaev.veles.session.FrameType
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class PairingFramesTest : PairingTestBase() {
    @Test
    fun round_trips_every_pairing_frame_type() {
        val cases = listOf(
            PairingFrameType.REQUEST to ByteArray(REQUEST_PAYLOAD),
            PairingFrameType.PA to ByteArray(OFFER_PAYLOAD),
            PairingFrameType.PB to ByteArray(POINT_BYTES),
            PairingFrameType.CA to ByteArray(CONFIRMATION_BYTES),
            PairingFrameType.CB to ByteArray(CONFIRMATION_BYTES),
        )

        for ((type, payload) in cases) {
            val frame = PairingFrames.encode(type, payload)
            assertEquals(type.code, frame[0].toInt())
            val decoded = PairingFrames.decode(frame)
            assertEquals(type, decoded.type)
            assertArrayEquals(payload, decoded.payload)
        }
    }

    @Test
    fun pairing_frame_types_are_0x01_through_0x05_and_do_not_overlap_reconnect_frames() {
        assertEquals(listOf(0x01, 0x02, 0x03, 0x04, 0x05), PairingFrameType.entries.map { it.code })
        val reconnectCodes = FrameType.entries.map { it.wire }.toSet()
        assertTrue(PairingFrameType.entries.none { it.code in reconnectCodes })
        PairingFrameType.entries.forEach { assertEquals(it, PairingFrameType.fromCode(it.code)) }
    }

    @Test
    fun rejects_unknown_reconnect_and_reject_frame_types() {
        for (type in listOf(0x00, 0x06, 0x07, 0x0F, 0xFF)) {
            val frame = byteArrayOf(type.toByte(), 0, 0)
            assertThrows("type 0x${type.toString(16)} is not a pairing frame", PairingFrameReject::class.java) {
                PairingFrames.decode(frame)
            }
        }
    }

    @Test
    fun rejects_length_disagreements_and_oversize_frames() {
        assertThrows(PairingFrameReject::class.java) { PairingFrames.decode(byteArrayOf(0x01, 0, 2, 7)) }
        assertThrows(PairingFrameReject::class.java) { PairingFrames.decode(byteArrayOf(0x01, 0, 1, 7, 8)) }
        assertThrows(PairingFrameReject::class.java) { PairingFrames.decode(byteArrayOf(0x01, 0)) }
        assertThrows(PairingFrameReject::class.java) {
            PairingFrames.encode(PairingFrameType.REQUEST, ByteArray(MAX_FRAME - HEADER_BYTES + 1))
        }
        assertThrows(PairingFrameReject::class.java) { PairingFrames.decode(ByteArray(MAX_FRAME + 1)) }
        assertEquals(MAX_FRAME, PairingFrames.encode(PairingFrameType.REQUEST, ByteArray(MAX_FRAME - HEADER_BYTES)).size)
    }

    private companion object {
        const val HEADER_BYTES = 3
        const val MAX_FRAME = 4096
        const val REQUEST_PAYLOAD = 21
        const val OFFER_PAYLOAD = 102
        const val POINT_BYTES = 65
        const val CONFIRMATION_BYTES = 32
    }
}
