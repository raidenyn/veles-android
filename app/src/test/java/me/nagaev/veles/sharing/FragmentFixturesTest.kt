package me.nagaev.veles.sharing

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertNull
import org.junit.Test

class FragmentFixturesTest {
    @OptIn(ExperimentalStdlibApi::class)
    @Test
    fun frozenFragmentVectorsUseProductionReceiver() {
        val text = checkNotNull(javaClass.getResourceAsStream("/fixtures/fragmentation.json"))
            .bufferedReader().use { it.readText() }
        for (element in Json.parseToJsonElement(text).jsonObject.getValue("fixtures").jsonArray) {
            val fixture = element.jsonObject
            val input = fixture.getValue("input").jsonObject
            val expected = fixture.getValue("expected").jsonObject.getValue("transactions").jsonArray
            val transport = GattTransport { 0L }
            transport.connect("peer")
            transport.setMtu("peer", input.getValue("mtu").jsonPrimitive.content.toInt())
            input.getValue("transactions").jsonArray.forEachIndexed { index, transaction ->
                var last: ByteArray? = null
                transaction.jsonObject.getValue("fragments").jsonArray.forEach { fragment ->
                    last = transport.receive("peer", fragment.jsonPrimitive.content.hexToByteArray()).record
                }
                val result = expected[index].jsonObject
                val bytes = result["reassembled"]?.jsonPrimitive?.content
                val id = fixture.getValue("id").jsonPrimitive.content
                if (bytes == null) assertNull(id, last) else assertArrayEquals(id, bytes.hexToByteArray(), last)
            }
        }
    }
}
