package me.nagaev.veles.sharing

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Byte-exact agreement of the production receiver and sender with the shared `fragmentation.json`. */
@OptIn(ExperimentalStdlibApi::class)
class FragmentFixturesTest {
    private fun fixtures(): List<JsonObject> {
        val text = checkNotNull(javaClass.getResourceAsStream("/fixtures/fragmentation.json"))
            .bufferedReader().use { it.readText() }
        return Json.parseToJsonElement(text).jsonObject.getValue("fixtures").jsonArray.map { it.jsonObject }
    }

    private fun mtu(fixture: JsonObject) = fixture.getValue("input").jsonObject.getValue("mtu").jsonPrimitive.content.toInt()

    @Test
    fun fragmentVectorsUseProductionReceiver() {
        for (fixture in fixtures()) {
            val input = fixture.getValue("input").jsonObject
            val expected = fixture.getValue("expected").jsonObject.getValue("transactions").jsonArray
            val transport = GattTransport { 0L }
            transport.connect("peer")
            transport.setMtu("peer", mtu(fixture))
            val id = fixture.getValue("id").jsonPrimitive.content
            input.getValue("transactions").jsonArray.forEachIndexed { index, transaction ->
                var last: ByteArray? = null
                transaction.jsonObject.getValue("fragments").jsonArray.forEach { fragment ->
                    last = transport.receive("peer", fragment.jsonPrimitive.content.hexToByteArray()).record
                }
                val bytes = expected[index].jsonObject["reassembled"]?.jsonPrimitive?.content
                if (bytes == null) assertNull(id, last) else assertArrayEquals(id, bytes.hexToByteArray(), last)
            }
        }
    }

    private class Canonical(val id: String, val mtu: Int, val record: String, val committed: List<String>)

    /** Accepted single-transaction vectors whose fragmentation is the canonical (no empty final) form. */
    private fun canonicalAcceptedVectors(): List<Canonical> = fixtures().mapNotNull { fixture ->
        val expected = fixture.getValue("expected").jsonObject
        val transactions = fixture.getValue("input").jsonObject.getValue("transactions").jsonArray
        val record = expected.getValue("transactions").jsonArray[0].jsonObject["reassembled"]?.jsonPrimitive?.content
        val committed = transactions[0].jsonObject.getValue("fragments").jsonArray.map { it.jsonPrimitive.content }
        // A trailing empty final fragment (8 hex digits) is receiver tolerance only; the sender never emits it.
        val canonical = expected.getValue("accepted").jsonPrimitive.content == "true" &&
            transactions.size == 1 && committed.last().length != EMPTY_FRAME_HEX
        if (canonical && record != null) {
            Canonical(fixture.getValue("id").jsonPrimitive.content, mtu(fixture), record, committed)
        } else {
            null
        }
    }

    /** The canonical sender must emit exactly the committed frames (transaction id aside) for every accepted vector. */
    @Test
    fun productionSenderReproducesCanonicalAcceptedVectors() {
        val vectors = canonicalAcceptedVectors()
        for (vector in vectors) {
            val sender = GattTransport { 0L }
            sender.connect("peer")
            sender.setMtu("peer", vector.mtu)
            assertTrue(vector.id, sender.enqueue("peer", vector.record.hexToByteArray()))
            val produced = mutableListOf<String>()
            while (true) {
                val frame = sender.nextFragment("peer") ?: break
                // byte 1 is the sender's own transaction id; every other byte must match the vector
                produced.add(frame.also { it[1] = 0 }.toHexString())
                sender.notificationComplete("peer", true)
            }
            val masked = vector.committed.map { hex -> hex.substring(0, 2) + "00" + hex.substring(4) }
            assertEquals(vector.id, masked, produced)
        }
        assertTrue("expected many canonical vectors, compared ${vectors.size}", vectors.size >= MIN_CANONICAL_VECTORS)
    }

    private companion object {
        const val EMPTY_FRAME_HEX = 8
        const val MIN_CANONICAL_VECTORS = 40
    }
}
