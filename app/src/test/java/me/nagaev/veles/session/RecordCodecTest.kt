package me.nagaev.veles.session

import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test
import java.nio.ByteBuffer

class RecordCodecTest {
    private val confirm = "veles-record-reconnect-confirm"
    private val push = "veles-record-push-event"

    @Test fun sealsFixtureRecords() {
        for (id in listOf(confirm, push)) {
            val vector = vector(id)
            val outputs = SessionFixtures.outputs(id)
            val sealed = vector.seal()

            assertArrayEquals(id, vector.record, sealed)
            assertArrayEquals(
                "$id header",
                bytes(outputs, "header"),
                sealed.copyOfRange(0, RecordCodec.HEADER),
            )
            assertArrayEquals(
                "$id ciphertext",
                bytes(outputs, "ciphertext"),
                sealed.copyOfRange(RecordCodec.HEADER, sealed.size - RecordCodec.TAG),
            )
            assertArrayEquals(
                "$id tag",
                bytes(outputs, "tag"),
                sealed.copyOfRange(sealed.size - RecordCodec.TAG, sealed.size),
            )

            val opened = vector.open(sealed, vector.sequence)
            assertEquals(vector.type, opened.type)
            assertEquals(vector.sequence, opened.sequence)
            assertArrayEquals(vector.body, opened.body)
        }
    }

    @Test fun rejectsNegRecordFixtures() {
        val ids = SessionFixtures.ids("veles-neg-record-")
        assertEquals(4, ids.size)
        for (id in ids) {
            val deviation = SessionFixtures.deviation(id)
            val baseId = SessionFixtures.str(deviation, "negates")
            val vector = vector(baseId)
            val tampered = when (SessionFixtures.str(deviation, "type")) {
                "bad_tag" -> vector.record.copyOf().also {
                    it[it.lastIndex] = (it.last().toInt() xor 1).toByte()
                }
                "mutated_session" -> vector.record.copyOf().also {
                    it[SESSION_OFFSET] = (it[SESSION_OFFSET].toInt() xor 1).toByte()
                }
                "mutated_direction" -> vector.record.copyOf().also {
                    it[DIRECTION_OFFSET] = Direction.PHONE_TO_CLIENT.wire.toByte()
                }
                "mutated_sequence" -> vector.record.copyOf().also {
                    ByteBuffer.wrap(it).putLong(SEQUENCE_OFFSET, (vector.sequence + 1UL).toLong())
                }
                else -> error("unexpected deviation for $id")
            }
            assertThrows(id, SessionReject::class.java) { vector.open(tampered, vector.sequence) }
        }
    }

    @Test fun tamperEveryHeaderAndCiphertextByteRejected() {
        val vector = vector(push)
        for (index in 0 until vector.record.size - RecordCodec.TAG) {
            val tampered = vector.record.copyOf()
            tampered[index] = (tampered[index].toInt() xor 1).toByte()
            assertThrows("byte $index", SessionReject::class.java) {
                vector.open(tampered, vector.sequence)
            }
        }
    }

    @Test fun rejectsLengthMismatch() {
        val vector = vector(confirm)
        val tampered = vector.record.copyOf()
        val ciphertextLength = tampered.size - RecordCodec.HEADER - RecordCodec.TAG
        ByteBuffer.wrap(tampered).putShort(LENGTH_OFFSET, (ciphertextLength - 1).toShort())
        assertThrows(SessionReject::class.java) { vector.open(tampered, vector.sequence) }
    }

    @Test fun rejects4097() {
        val vector = vector(push)
        val oversized = vector.record.copyOf(4097)
        assertEquals(4097, oversized.size)
        assertThrows(SessionReject::class.java) { vector.open(oversized, vector.sequence) }
        assertThrows(SessionReject::class.java) {
            RecordCodec.seal(
                vector.key,
                vector.prefix,
                vector.sid,
                vector.direction,
                vector.type,
                vector.sequence,
                ByteArray(RecordCodec.MAX_CIPHERTEXT + 1),
            )
        }
    }

    @Test fun rejectsUnknownAppErrorCode() {
        val vector = vector(confirm)
        val unknownCode = byteArrayOf(0x06, 0x00, 0x00)
        val unknownRecord = RecordCodec.seal(
            vector.key,
            vector.prefix,
            vector.sid,
            vector.direction,
            RecordType.APP_ERROR.wire,
            0UL,
            unknownCode,
        )
        assertThrows(SessionReject::class.java) { vector.open(unknownRecord, 0UL) }

        val validBody = byteArrayOf(AppErrorCode.PROTOCOL_VIOLATION.wire.toByte(), 0x00, 0x00)
        val validRecord = RecordCodec.seal(
            vector.key,
            vector.prefix,
            vector.sid,
            vector.direction,
            RecordType.APP_ERROR.wire,
            0UL,
            validBody,
        )
        assertArrayEquals(validBody, vector.open(validRecord, 0UL).body)
    }

    private data class Vector(
        val key: ByteArray,
        val prefix: ByteArray,
        val sid: ByteArray,
        val direction: Direction,
        val type: Int,
        val sequence: ULong,
        val body: ByteArray,
        val record: ByteArray,
    ) {
        fun seal(): ByteArray = RecordCodec.seal(key, prefix, sid, direction, type, sequence, body)

        fun open(record: ByteArray, expected: ULong): OpenedRecord = RecordCodec.open(key, prefix, sid, direction, expected, record)
    }

    private fun vector(id: String): Vector {
        val input = SessionFixtures.input(id)
        return Vector(
            key = bytes(input, "key"),
            prefix = bytes(input, "nonce_prefix"),
            sid = bytes(input, "session_id_prefix"),
            direction = checkNotNull(
                Direction.fromWire(SessionFixtures.wire(SessionFixtures.str(input, "direction"))),
            ) { "unknown fixture direction" },
            type = SessionFixtures.wire(SessionFixtures.str(input, "record_type")),
            sequence = SessionFixtures.str(input, "sequence").toULong(),
            body = bytes(input, "body"),
            record = bytes(SessionFixtures.outputs(id), "record"),
        )
    }

    private fun bytes(source: JsonObject, key: String): ByteArray = SessionFixtures.bytes(source, key)

    private companion object {
        const val DIRECTION_OFFSET = 2
        const val SEQUENCE_OFFSET = 5
        const val LENGTH_OFFSET = 13
        const val SESSION_OFFSET = 15
    }
}
