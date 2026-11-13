package me.nagaev.veles.session

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import org.junit.Assert.assertEquals
import org.junit.Test

class SessionLimitsTest {
    @Test
    fun matchesLimitsJson() {
        val limits = limits()
        val idleSeconds = number(limits, "reconnect_idle_timeout_seconds")
        val heartbeatSeconds = number(limits, "heartbeat_min_interval_seconds")
        assertEquals(180L, idleSeconds)
        assertEquals(30L, heartbeatSeconds)
        assertEquals(idleSeconds, SessionLimits.IDLE_TIMEOUT_MS / MILLIS_PER_SECOND)
        assertEquals(heartbeatSeconds, SessionLimits.HEARTBEAT_MIN_INTERVAL_MS / MILLIS_PER_SECOND)
        assertEquals(SessionLimits.SEQUENCE_MAX.toString(), text(limits, "sequence_max"))
    }

    private fun limits(): Map<String, JsonPrimitive> {
        val stream = checkNotNull(javaClass.getResourceAsStream("/limits.json")) { "missing /limits.json" }
        val root = Json.parseToJsonElement(stream.use { it.readBytes().decodeToString() }).jsonObject
        return root.getValue("limits").jsonArray.associate {
            val limit = it.jsonObject
            limit.getValue("name").jsonPrimitive.content to limit.getValue("value").jsonPrimitive
        }
    }

    private fun number(
        limits: Map<String, JsonPrimitive>,
        name: String,
    ): Long = checkNotNull(limits[name]) { "missing limit $name" }.long

    private fun text(
        limits: Map<String, JsonPrimitive>,
        name: String,
    ): String = checkNotNull(limits[name]) { "missing limit $name" }.content

    private companion object {
        const val MILLIS_PER_SECOND = 1_000L
    }
}
