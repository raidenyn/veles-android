package me.nagaev.veles.session

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject

/**
 * Test-only reader for the shared `protocol/v1/fixtures/crypto.json`.
 *
 * The unit-test source set maps the repository's single `protocol/v1` fixture root; this helper
 * never copies or rewrites fixture truth.
 */
internal object SessionFixtures {
    const val RECONNECT = "veles-reconnect-authentication"
    const val RECORD_CONFIRM = "veles-record-reconnect-confirm"
    const val RECORD_PUSH = "veles-record-push-event"

    private val all: List<JsonObject> by lazy {
        val stream = checkNotNull(SessionFixtures::class.java.getResourceAsStream("/fixtures/crypto.json")) {
            "missing fixture resource /fixtures/crypto.json"
        }
        val text = stream.use { it.readBytes().decodeToString() }
        Json.parseToJsonElement(text).jsonObject.getValue("fixtures").jsonArray.map { it.jsonObject }
    }

    fun ids(prefix: String): List<String> = all.map { str(it, "id") }.filter { it.startsWith(prefix) }

    fun byId(id: String): JsonObject = all.firstOrNull { str(it, "id") == id } ?: error("missing fixture id $id")

    fun input(id: String): JsonObject = obj(byId(id), "input")

    fun outputs(id: String): JsonObject = obj(obj(byId(id), "expected"), "outputs")

    fun deviation(id: String): JsonObject = obj(input(id), "deviation")

    fun str(json: JsonObject, key: String): String = primitive(json, key).content

    fun bytes(json: JsonObject, key: String): ByteArray = hex(str(json, key))

    fun obj(json: JsonObject, key: String): JsonObject = checkNotNull(json[key] as? JsonObject) { "missing fixture object $key" }

    private fun primitive(json: JsonObject, key: String): JsonPrimitive {
        val value = checkNotNull(json[key] as? JsonPrimitive) { "missing fixture string field $key" }
        return value
    }

    fun caps(json: JsonObject, key: String): ByteArray {
        val array = checkNotNull(json[key] as? JsonArray) { "missing fixture caps array $key" }
        return array.map { wire((it as JsonPrimitive).content).toByte() }.toByteArray()
    }

    fun wire(value: String): Int = value.removePrefix("0x").toInt(16)

    fun hex(value: String): ByteArray {
        val text = value.removePrefix("0x")
        require(text.length % 2 == 0 && text.all { it in '0'..'9' || it in 'a'..'f' }) {
            "malformed fixture hex"
        }
        return text.chunked(2).map { it.toInt(16).toByte() }.toByteArray()
    }

    fun hex(bytes: ByteArray): String = bytes.joinToString("") { it.toUByte().toString(16).padStart(2, '0') }
}
