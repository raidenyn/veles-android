package me.nagaev.veles.bluetoothspike

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.util.UUID

@Serializable
internal data class SpikeWireMessage(
    val type: String,
    val clientNonce: String? = null,
    val serverNonce: String? = null,
    val sessionId: String? = null,
    val proof: String? = null,
    val delivery: String? = null,
    val eventId: Int? = null,
    val code: String? = null,
    val merchant: String? = null,
    val amount: String? = null,
    val currency: String? = null,
    val phoneLabel: String? = null,
    val errorCode: String? = null,
)

internal object SpikeProtocol {
    val SERVICE_UUID: UUID = UUID.fromString("7e570001-6f74-702d-7370-696b65000001")
    val COMMAND_UUID: UUID = UUID.fromString("7e570002-6f74-702d-7370-696b65000001")
    val EVENT_UUID: UUID = UUID.fromString("7e570003-6f74-702d-7370-696b65000001")
    val CCC_UUID: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")

    const val FRAME_VERSION = 1
    const val MAX_FRAME_BYTES = 20
    const val FRAME_HEADER_BYTES = 6
    const val FRAME_PAYLOAD_BYTES = MAX_FRAME_BYTES - FRAME_HEADER_BYTES
    const val MAX_CHUNKS = 32
    const val MAX_MESSAGE_BYTES = FRAME_PAYLOAD_BYTES * MAX_CHUNKS
    const val REASSEMBLY_TIMEOUT_MILLIS = 5_000L

    const val TYPE_HELLO = "hello"
    const val TYPE_CHALLENGE = "challenge"
    const val TYPE_AUTHENTICATE = "authenticate"
    const val TYPE_AUTHENTICATED = "authenticated"
    const val TYPE_PULL = "pull"
    const val TYPE_OTP = "otp"
    const val TYPE_HEARTBEAT = "heartbeat"
    const val TYPE_ERROR = "error"
    const val DELIVERY_CURRENT = "current"
    const val DELIVERY_PUSH = "push"

    private val json = Json {
        encodeDefaults = false
        explicitNulls = false
        ignoreUnknownKeys = false
    }

    fun encode(message: SpikeWireMessage): ByteArray = json.encodeToString(SpikeWireMessage.serializer(), message).encodeToByteArray()

    fun decode(bytes: ByteArray): SpikeWireMessage = json.decodeFromString(SpikeWireMessage.serializer(), bytes.decodeToString())
}
