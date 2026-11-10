package me.nagaev.veles.session

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class PreAuthCodecTest {
    @Test fun rejectsWrongLength() {
        val frame = byteArrayOf(FrameType.RECONNECT_HELLO.wire.toByte(), 0x00, 0x05, 0x01, 0x02, 0x03)
        assertThrows(SessionReject::class.java) { PreAuthCodec.decode(frame) }
    }

    @Test fun rejectsTrailingBytes() {
        val encoded = PreAuthCodec.encode(FrameType.RECONNECT_HELLO.wire, byteArrayOf(0x01, 0x02, 0x03))
        assertThrows(SessionReject::class.java) { PreAuthCodec.decode(encoded + byteArrayOf(0x00)) }
    }

    @Test fun rejectsOver4096() {
        val payload = ByteArray(4094)
        val frame = byteArrayOf(FrameType.RECONNECT_HELLO.wire.toByte(), 0x0F, 0xFE.toByte()) + payload
        assertEquals(4097, frame.size)
        assertThrows(SessionReject::class.java) { PreAuthCodec.decode(frame) }
        assertThrows(SessionReject::class.java) { PreAuthCodec.encode(FrameType.RECONNECT_HELLO.wire, payload) }
    }

    @Test fun rejectsUnknownType() {
        for (type in listOf(0x08, 0x10)) {
            assertThrows("encode $type", SessionReject::class.java) {
                PreAuthCodec.encode(type, byteArrayOf())
            }
            assertThrows("decode $type", SessionReject::class.java) {
                PreAuthCodec.decode(byteArrayOf(type.toByte(), 0x00, 0x00))
            }
        }
    }
}
