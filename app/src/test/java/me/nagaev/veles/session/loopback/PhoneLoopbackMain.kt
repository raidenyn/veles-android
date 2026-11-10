package me.nagaev.veles.session.loopback

import kotlinx.coroutines.runBlocking
import me.nagaev.veles.session.ApplicationHandler
import me.nagaev.veles.session.IdentitySigner
import me.nagaev.veles.session.LocalRecordState
import me.nagaev.veles.session.P256
import me.nagaev.veles.session.PeerLookup
import me.nagaev.veles.session.PeerRecord
import me.nagaev.veles.session.PhoneSessionEngine
import me.nagaev.veles.session.RecordType
import me.nagaev.veles.session.SessionClass
import me.nagaev.veles.session.SessionEffect
import me.nagaev.veles.session.TestPeers
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.math.BigInteger
import java.nio.ByteBuffer
import java.security.MessageDigest
import kotlin.system.exitProcess

/**
 * OTP-09 Task 10 cross-endpoint loopback: the real Kotlin phone engine over a byte pipe.
 *
 * Protocol (stdin `kind u8 || u32be length || bytes`):
 * - kind 0: a transport frame or protected record for the phone engine
 * - kind 1: a UTF-8 command, `push` (send one push_event) or `reset` (start a new generation)
 *
 * stdout: kind 0 frames for the client and kind 2 events:
 * - `0x01 || class u8 || session_id_prefix[8]` — authenticated
 * - `0x02 || session_id_prefix[8]` — closed
 *
 * stderr is diagnostics only. Identities are derived from the seed in argv[1]; the TypeScript
 * test derives the same client identity and phone trust record from that seed.
 */
private const val KIND_BYTES = 0
private const val KIND_COMMAND = 1
private const val KIND_EVENT = 2

private const val EVENT_AUTHENTICATED = 0x01
private const val EVENT_CLOSED = 0x02

private const val SESSION_PREFIX_BYTES = 8
private const val INSTALLATION_BYTES = 16
private const val EVENT_ID_BYTES = 16

@Suppress("CyclomaticComplexMethod", "LoopWithTooManyJumpStatements")
fun main(args: Array<String>) {
    if (args.size != 1) {
        System.err.println("usage: PhoneLoopbackMainKt <seed>")
        exitProcess(2)
    }
    val seed = args[0]
    val phone = LoopbackIdentity.derive("Veles loopback phone identity v1", "Veles loopback phone installation v1", seed)
    val client = LoopbackIdentity.derive("Veles loopback client identity v1", "Veles loopback client installation v1", seed)
    val trustRecord = PeerRecord(client.installationId, client.publicKey, LocalRecordState.ACTIVE)
    val handler = LoopbackHandler(phone.installationId, seed)
    val input = DataInputStream(BufferedInputStream(System.`in`))
    val output = DataOutputStream(BufferedOutputStream(System.out))
    var currentPrefix: ByteArray? = null
    var eventCounter = 0

    fun emit(kind: Int, payload: ByteArray) {
        synchronized(output) {
            output.writeByte(kind)
            output.writeInt(payload.size)
            output.write(payload)
            output.flush()
        }
    }

    fun emitClosed(prefix: ByteArray?) {
        emit(KIND_EVENT, byteArrayOf(EVENT_CLOSED.toByte()) + (prefix ?: ByteArray(SESSION_PREFIX_BYTES)))
    }

    var engine: PhoneSessionEngine = newEngine(phone, trustRecord, handler)

    suspend fun apply(effects: List<SessionEffect>) {
        for (effect in effects) {
            when (effect) {
                is SessionEffect.Send -> {
                    emit(KIND_BYTES, effect.bytes)
                    effect.completionToken?.let { token -> apply(engine.onSendComplete(token, true)) }
                }
                is SessionEffect.Authenticated -> {
                    currentPrefix = effect.sessionIdPrefix.copyOf()
                    emit(
                        KIND_EVENT,
                        byteArrayOf(EVENT_AUTHENTICATED.toByte(), classCode(effect.sessionClass)) + effect.sessionIdPrefix,
                    )
                }
                is SessionEffect.Deliver -> Unit
                is SessionEffect.Terminated -> System.err.println("loopback: terminated ${effect.reason}")
                SessionEffect.Close -> {
                    emitClosed(currentPrefix)
                    currentPrefix = null
                }
            }
        }
    }

    runBlocking {
        while (true) {
            val kind = try {
                input.readUnsignedByte()
            } catch (_: Exception) {
                break
            }
            val length = try {
                input.readInt()
            } catch (_: Exception) {
                System.err.println("loopback: truncated length")
                break
            }
            if (length < 0 || length > MAX_MESSAGE) {
                System.err.println("loopback: invalid length $length")
                break
            }
            val payload = ByteArray(length)
            try {
                input.readFully(payload)
            } catch (_: Exception) {
                System.err.println("loopback: truncated payload")
                break
            }
            when (kind) {
                KIND_BYTES -> apply(engine.onInbound(payload))
                KIND_COMMAND -> when (payload.decodeToString()) {
                    "push" -> {
                        val event = handler.newEvent(++eventCounter)
                        apply(engine.sendApplication(RecordType.PUSH_EVENT.wire, event))
                    }
                    "reset" -> {
                        engine.teardown()
                        emitClosed(currentPrefix)
                        currentPrefix = null
                        handler.reset()
                        engine = newEngine(phone, trustRecord, handler)
                    }
                    else -> System.err.println("loopback: unknown command")
                }
                else -> System.err.println("loopback: unknown kind $kind")
            }
        }
    }
}

private fun newEngine(
    phone: LoopbackIdentity,
    record: PeerRecord,
    handler: ApplicationHandler,
): PhoneSessionEngine = PhoneSessionEngine(
    localInstallationId = phone.installationId,
    signer = phone.signer,
    lookup = PeerLookup { id -> record.takeIf { it.installationId.contentEquals(id) } },
    clock = { System.nanoTime() / 1_000_000L },
    nonce = { ByteArray(NONCE_BYTES).also { java.security.SecureRandom().nextBytes(it) } },
    handler = handler,
)

private fun classCode(value: SessionClass): Byte = when (value) {
    SessionClass.FULL -> 0x00
    SessionClass.FULL_PENDING_PEER -> 0x01
    SessionClass.ACTIVATION_RECOVERY -> 0x02
}.toByte()

/** Answers one pull with one §8.1 event and records acknowledged event ids. */
private class LoopbackHandler(
    private val phoneInstallationId: ByteArray,
    private val seed: String,
) : ApplicationHandler {
    private val known = mutableSetOf<String>()
    var settled: Boolean = false
        private set

    fun reset() {
        known.clear()
        settled = false
    }

    fun newEvent(counter: Int): ByteArray = syntheticEvent(counter).also { known += eventId(it) }

    override fun handles(type: Int): Boolean = type == RecordType.PULL_REQUEST.wire || type == RecordType.EVENT_ACK.wire

    override suspend fun handle(type: Int, body: ByteArray, reply: suspend (Int, ByteArray) -> Unit) {
        when (type) {
            RecordType.PULL_REQUEST.wire -> {
                val event = syntheticEvent(0)
                known += eventId(event)
                reply(RecordType.PULL_EVENTS.wire, byteArrayOf(1) + event)
            }
            RecordType.EVENT_ACK.wire -> {
                if (body.size == ACK_BYTES) {
                    val id = body.copyOfRange(INSTALLATION_BYTES, ACK_BYTES).joinToString("") {
                        it.toUByte().toString(16).padStart(2, '0')
                    }
                    if (id in known) {
                        settled = true
                        System.err.println("loopback: settled $id")
                    } else {
                        System.err.println("loopback: unknown event ack")
                    }
                }
            }
        }
    }

    private fun eventId(event: ByteArray): String {
        val id = event.copyOfRange(INSTALLATION_BYTES, INSTALLATION_BYTES + EVENT_ID_BYTES)
        return id.joinToString("") { it.toUByte().toString(16).padStart(2, '0') }
    }

    private fun syntheticEvent(counter: Int): ByteArray {
        val id = MessageDigest.getInstance("SHA-256")
            .digest(
                "Veles loopback event v1".toByteArray() + ByteArray(4).also {
                    ByteBuffer.wrap(it).putInt(counter)
                } + seed.toByteArray(),
            )
            .copyOfRange(0, EVENT_ID_BYTES)
        return phoneInstallationId +
            id +
            ByteBuffer.allocate(8).putLong(1_700_000_000L + counter).array() +
            ByteBuffer.allocate(4).putInt(120).array() +
            byteArrayOf(0, 6) + "123456".toByteArray() +
            byteArrayOf(0, 13) + "Test merchant".toByteArray() +
            ByteBuffer.allocate(8).putLong(100).array() +
            byteArrayOf(2) +
            "XTS".toByteArray()
    }

    private companion object {
        const val ACK_BYTES = 32
    }
}

/** A software P-256 identity derived from the loopback seed. */
private class LoopbackIdentity private constructor(
    val installationId: ByteArray,
    val publicKey: ByteArray,
    privateKey: java.security.PrivateKey,
) {
    val signer: IdentitySigner = IdentitySigner { message -> TestPeers.signDer(privateKey, message) }

    companion object {
        fun derive(identityLabel: String, installationLabel: String, seed: String): LoopbackIdentity {
            val (scalar, point) = LoopbackCrypto.identity(identityLabel, seed)
            val keyPair = java.security.KeyPair(P256.requireValidPoint(point), P256.privateFromScalar(scalar))
            val installationId = MessageDigest.getInstance("SHA-256")
                .digest(installationLabel.toByteArray() + seed.toByteArray())
                .copyOfRange(0, INSTALLATION_BYTES)
            return LoopbackIdentity(installationId, point, keyPair.private)
        }
    }
}

/** Deterministic P-256 scalar derivation and the scalar multiplication the JCA does not expose. */
private object LoopbackCrypto {
    private val P = BigInteger("ffffffff00000001000000000000000000000000ffffffffffffffffffffffff", 16)
    private val A = P.subtract(BigInteger.valueOf(3))
    private val N = BigInteger("ffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551", 16)
    private val GX = BigInteger("6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296", 16)
    private val GY = BigInteger("4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5", 16)

    fun identity(label: String, seed: String): Pair<ByteArray, ByteArray> {
        var counter = 0
        while (counter < MAX_DERIVATION_ATTEMPTS) {
            val digest = MessageDigest.getInstance("SHA-256")
                .digest(label.toByteArray() + byteArrayOf(counter.toByte()) + seed.toByteArray())
            val scalar = BigInteger(1, digest)
            if (scalar.signum() > 0 && scalar < N) {
                val (x, y) = multiply(scalar)
                return digest to (byteArrayOf(0x04) + coordinate(x) + coordinate(y))
            }
            counter += 1
        }
        error("loopback: cannot derive a P-256 scalar from the seed")
    }

    private fun multiply(k: BigInteger): Pair<BigInteger, BigInteger> {
        var resultX: BigInteger? = null
        var resultY: BigInteger? = null
        var addendX: BigInteger? = GX
        var addendY: BigInteger? = GY
        for (bit in 0 until k.bitLength()) {
            if (k.testBit(bit)) {
                val (x, y) = add(resultX, resultY, addendX, addendY)
                resultX = x
                resultY = y
            }
            val (x, y) = add(addendX, addendY, addendX, addendY)
            addendX = x
            addendY = y
        }
        return checkNotNull(resultX) to checkNotNull(resultY)
    }

    private fun add(
        x1: BigInteger?,
        y1: BigInteger?,
        x2: BigInteger?,
        y2: BigInteger?,
    ): Pair<BigInteger?, BigInteger?> {
        if (x1 == null) return x2 to y2
        if (x2 == null) return x1 to y1
        val ax = x1
        val ay = checkNotNull(y1)
        val bx = x2
        val by = checkNotNull(y2)
        if (ax == bx && (ay + by).mod(P).signum() == 0) return null to null
        val slope = if (ax == bx && ay == by) {
            (ax.multiply(ax).multiply(BigInteger.valueOf(3)).add(A))
                .multiply(ay.multiply(BigInteger.TWO).mod(P).modInverse(P))
        } else {
            by.subtract(ay).multiply(bx.subtract(ax).modInverse(P))
        }.mod(P)
        val x3 = slope.multiply(slope).subtract(ax).subtract(bx).mod(P)
        val y3 = slope.multiply(ax.subtract(x3)).subtract(ay).mod(P)
        return x3 to y3
    }

    private fun coordinate(value: BigInteger): ByteArray {
        val raw = value.toByteArray()
        return when {
            raw.size == COORDINATE_BYTES -> raw
            raw.size > COORDINATE_BYTES -> raw.copyOfRange(raw.size - COORDINATE_BYTES, raw.size)
            else -> ByteArray(COORDINATE_BYTES - raw.size) + raw
        }
    }

    private const val COORDINATE_BYTES = 32
    private const val MAX_DERIVATION_ATTEMPTS = 256
}

private const val MAX_MESSAGE = 1 shl 20
private const val NONCE_BYTES = 16
