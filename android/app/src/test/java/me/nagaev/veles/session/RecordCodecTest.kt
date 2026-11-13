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
        // Sealed without the codec's body rules so the open path's own check is exercised.
        val unknownRecord = vector.rawSeal(RecordType.APP_ERROR.wire, 0UL, unknownCode)
        assertThrows(SessionReject::class.java) { vector.open(unknownRecord, 0UL) }

        val validBody = byteArrayOf(AppErrorCode.PROTOCOL_VIOLATION.wire.toByte(), 0x00, 0x00)
        val validRecord = vector.sealWith(RecordType.APP_ERROR.wire, 0UL, validBody)
        assertArrayEquals(validBody, vector.open(validRecord, 0UL).body)
    }

    @Test fun fixedBodyRulesApplyToSeal() {
        val vector = vector(confirm)
        // §7.3: empty-body framework and request records.
        for (type in listOf(
            RecordType.APP_HEARTBEAT.wire,
            RecordType.PULL_REQUEST.wire,
            RecordType.REMOVAL_REQUEST.wire,
            RecordType.REMOVAL_RESPONSE.wire,
        )) {
            assertThrows("$type must be empty", SessionReject::class.java) {
                vector.sealWith(type, 0UL, byteArrayOf(1))
            }
        }
        // Fixed-width records: the confirm is 33 bytes, every MAC and ack is 32.
        assertThrows(SessionReject::class.java) {
            vector.sealWith(RecordType.RECONNECT_CONFIRM.wire, 0UL, ByteArray(32))
        }
        for (type in listOf(
            RecordType.ACTIVATION_COMMIT.wire,
            RecordType.ACTIVATION_ACK.wire,
            RecordType.EVENT_ACK.wire,
        )) {
            assertThrows("$type is fixed width", SessionReject::class.java) {
                vector.sealWith(type, 0UL, ByteArray(31))
            }
        }
        val phoneVector = vector(push)
        for (type in listOf(
            RecordType.ACTIVATION_COMPLETE.wire,
            RecordType.ENROLLMENT_PREPARE.wire,
        )) {
            assertThrows("$type is fixed width", SessionReject::class.java) {
                phoneVector.sealWith(type, 0UL, ByteArray(31))
            }
        }
        assertThrows("reserved policy bits", SessionReject::class.java) {
            phoneVector.sealWith(RecordType.PHONE_POLICY_STATE.wire, 0UL, byteArrayOf(0, 0x10))
        }
        assertThrows("unknown policy", SessionReject::class.java) {
            phoneVector.sealWith(RecordType.PHONE_POLICY_STATE.wire, 0UL, byteArrayOf(3, 0))
        }
    }

    @Test fun appErrorReasonIsBoundedSafeText() {
        val vector = vector(confirm)
        fun body(reason: ByteArray) = byteArrayOf(
            AppErrorCode.PROTOCOL_VIOLATION.wire.toByte(),
            (reason.size ushr 8).toByte(),
            reason.size.toByte(),
        ) + reason

        // Boundary: 64 bytes of printable text is accepted on both seal and open.
        val longest = ByteArray(64) { 'a'.code.toByte() }
        val sealed = vector.sealWith(RecordType.APP_ERROR.wire, 0UL, body(longest))
        assertArrayEquals(body(longest), vector.open(sealed, 0UL).body)

        assertThrows("65 bytes exceeds the reason bound", SessionReject::class.java) {
            vector.sealWith(RecordType.APP_ERROR.wire, 0UL, body(ByteArray(65) { 'a'.code.toByte() }))
        }
        for (control in listOf(0x00, 0x01, 0x1F, 0x7F)) {
            assertThrows("U+%04X is prohibited".format(control), SessionReject::class.java) {
                vector.sealWith(RecordType.APP_ERROR.wire, 0UL, body(byteArrayOf(0x41, control.toByte(), 0x42)))
            }
        }
        // The same rules gate the open path, not just seal.
        val rejected = vector.rawSeal(RecordType.APP_ERROR.wire, 0UL, body(byteArrayOf(0x41, 0x00, 0x42)))
        assertThrows(SessionReject::class.java) { vector.open(rejected, 0UL) }
    }

    @Test fun openAppliesFixedBodyRules() {
        val vector = vector(confirm)
        // A peer can put any plaintext inside a valid AEAD record; the open path must apply the
        // same §7.3 body rules. These records are sealed without the codec's body check.
        val heartbeat = vector.rawSeal(RecordType.APP_HEARTBEAT.wire, 0UL, byteArrayOf(1))
        assertThrows(SessionReject::class.java) { vector.open(heartbeat, 0UL) }

        val pull = vector.rawSeal(RecordType.PULL_REQUEST.wire, 0UL, byteArrayOf(1, 2))
        assertThrows(SessionReject::class.java) { vector.open(pull, 0UL) }

        val ack = vector.rawSeal(RecordType.EVENT_ACK.wire, 0UL, ByteArray(31))
        assertThrows(SessionReject::class.java) { vector.open(ack, 0UL) }
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

        @Suppress("MaxLineLength") // ktlint keeps this expression body on one line.
        fun sealWith(type: Int, sequence: ULong, body: ByteArray): ByteArray = RecordCodec.seal(key, prefix, sid, direction, type, sequence, body)

        /** A valid AEAD record whose plaintext body bypasses the §7.3 body rules. */
        fun rawSeal(type: Int, sequence: ULong, body: ByteArray): ByteArray {
            val header = ByteArray(RecordCodec.HEADER)
            header[0] = 1
            header[1] = 1
            header[2] = direction.wire.toByte()
            header[3] = (type ushr 8).toByte()
            header[4] = type.toByte()
            ByteBuffer.wrap(header).putLong(5, sequence.toLong())
            header[13] = (body.size ushr 8).toByte()
            header[14] = body.size.toByte()
            sid.copyInto(header, 15)
            val nonce = ByteArray(12)
            prefix.copyInto(nonce, 0)
            ByteBuffer.wrap(nonce).putLong(4, sequence.toLong())
            val cipher = javax.crypto.Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(
                javax.crypto.Cipher.ENCRYPT_MODE,
                javax.crypto.spec.SecretKeySpec(key, "AES"),
                javax.crypto.spec.GCMParameterSpec(128, nonce),
            )
            cipher.updateAAD(header)
            return header + cipher.doFinal(body)
        }

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
