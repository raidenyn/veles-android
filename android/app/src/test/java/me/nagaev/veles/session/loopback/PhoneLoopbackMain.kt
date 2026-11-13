package me.nagaev.veles.session.loopback

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.addJsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import me.nagaev.veles.pairing.ActivationEngine
import me.nagaev.veles.pairing.Outgoing
import me.nagaev.veles.pairing.PairingAdmission
import me.nagaev.veles.pairing.PairingLimits
import me.nagaev.veles.pairing.PairingState
import me.nagaev.veles.pairing.PairingWindow
import me.nagaev.veles.pairing.PhonePairingMachine
import me.nagaev.veles.pairing.ProofRegistry
import me.nagaev.veles.pairing.VelesPairingCore
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
import me.nagaev.veles.trust.ActivationVerifier
import me.nagaev.veles.trust.IdentityKeyStore
import me.nagaev.veles.trust.InstallationIdentity
import me.nagaev.veles.trust.InstallationTrustRepository
import me.nagaev.veles.trust.TrustRepository
import me.nagaev.veles.trust.TrustSnapshot
import me.nagaev.veles.trust.TrustState
import me.nagaev.veles.trust.TrustStorage
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.math.BigInteger
import java.nio.ByteBuffer
import java.security.MessageDigest
import java.security.SecureRandom
import kotlin.system.exitProcess

/**
 * OTP-09 Task 10 cross-endpoint loopback: the real Kotlin phone engine over a byte pipe.
 *
 * Protocol (stdin `kind u8 || u32be length || bytes`):
 * - kind 0: a transport frame or protected record for the phone engine
 * - kind 1: a UTF-8 command
 *
 * stdout: kind 0 frames for the client and kind 2 events:
 * - `0x01 || class u8 || session_id_prefix[8]` — authenticated
 * - `0x02 || session_id_prefix[8]` — closed
 * - `0x03 || pairing_id[16]` — a pairing window opened (OTP-10)
 * - `0x04 || UTF-8 JSON` — the phone's trust snapshot (OTP-10)
 * - `0x05 || UTF-8 state` — the pairing machine's state (OTP-10)
 *
 * stderr is diagnostics only. Identities are derived from the seed in argv[1]; the TypeScript
 * test derives the same client identity and phone trust record from that seed.
 *
 * OTP-10 adds an optional `pairing` mode. There the production [PhonePairingMachine] owns the
 * enrollment connection with a fixed [SecureRandom] that draws the code `000042`, and the
 * production [TrustRepository] is the durable store. After a verified activation the same
 * store drives the real reconnect engine and [ActivationEngine] for the recovery scenario.
 */
private const val KIND_BYTES = 0
private const val KIND_COMMAND = 1
private const val KIND_EVENT = 2

private const val EVENT_AUTHENTICATED = 0x01
private const val EVENT_CLOSED = 0x02
private const val EVENT_WINDOW = 0x03
private const val EVENT_TRUST = 0x04
private const val EVENT_PAIRING_STATE = 0x05

private const val SESSION_PREFIX_BYTES = 8
private const val INSTALLATION_BYTES = 16
private const val EVENT_ID_BYTES = 16
private const val NONCE_BYTES = 16
private const val MAX_MESSAGE = 1 shl 20
private const val MODE_PAIRING = "pairing"
private const val BOOT_MARKER = "loopback-boot"
private const val CONTINUITY_TOKEN = "abababababababababababababababababababababababababababababababab"

fun main(args: Array<String>) {
    if (args.isEmpty() || args.size > 2) {
        System.err.println("usage: PhoneLoopbackMainKt <seed> [pairing]")
        exitProcess(2)
    }
    val seed = args[0]
    val pairing = args.getOrNull(1) == MODE_PAIRING
    val phone = LoopbackIdentity.derive("Veles loopback phone identity v1", "Veles loopback phone installation v1", seed)
    val input = DataInputStream(BufferedInputStream(System.`in`))
    val output = LoopbackOutput(DataOutputStream(BufferedOutputStream(System.out)))
    if (pairing) {
        PairingLoopback(seed, phone, input, output).run()
    } else {
        val client = LoopbackIdentity.derive("Veles loopback client identity v1", "Veles loopback client installation v1", seed)
        SessionLoopback(seed, phone, client, input, output).run()
    }
}

/** Serialized stdout writer; one frame or event per call, flushed before returning. */
private class LoopbackOutput(private val output: DataOutputStream) {
    fun frame(bytes: ByteArray) = emit(KIND_BYTES, bytes)

    fun authenticated(classCode: Byte, prefix: ByteArray) = event(EVENT_AUTHENTICATED, byteArrayOf(classCode) + prefix)

    fun closed(prefix: ByteArray?) = event(EVENT_CLOSED, prefix ?: ByteArray(SESSION_PREFIX_BYTES))

    fun window(pairingId: ByteArray) = event(EVENT_WINDOW, pairingId)

    fun state(name: String) = event(EVENT_PAIRING_STATE, name.toByteArray(Charsets.UTF_8))

    fun trust(json: String) = event(EVENT_TRUST, json.toByteArray(Charsets.UTF_8))

    private fun event(code: Int, payload: ByteArray) = emit(KIND_EVENT, byteArrayOf(code.toByte()) + payload)

    private fun emit(kind: Int, payload: ByteArray) {
        synchronized(output) {
            output.writeByte(kind)
            output.writeInt(payload.size)
            output.write(payload)
            output.flush()
        }
    }
}

@Suppress("ReturnCount") // One branch per truncated stream frame.
private fun readMessage(input: DataInputStream): Pair<Int, ByteArray>? {
    val kind = try {
        input.readUnsignedByte()
    } catch (_: Exception) {
        return null
    }
    val length = try {
        input.readInt()
    } catch (_: Exception) {
        System.err.println("loopback: truncated length")
        return null
    }
    if (length < 0 || length > MAX_MESSAGE) {
        System.err.println("loopback: invalid length $length")
        return null
    }
    val payload = ByteArray(length)
    try {
        input.readFully(payload)
    } catch (_: Exception) {
        System.err.println("loopback: truncated payload")
        return null
    }
    return kind to payload
}

/** The OTP-09 reconnect-only loopback: one synthetic ACTIVE peer, no pairing store. */
private class SessionLoopback(
    private val seed: String,
    private val phone: LoopbackIdentity,
    client: LoopbackIdentity,
    private val input: DataInputStream,
    private val output: LoopbackOutput,
) {
    private val trustRecord = PeerRecord(client.installationId, client.publicKey, LocalRecordState.ACTIVE)
    private val handler = LoopbackHandler(phone.installationId, seed)
    private var currentPrefix: ByteArray? = null
    private var eventCounter = 0
    private var engine: PhoneSessionEngine = newEngine(phone, trustRecord, handler)

    @Suppress("CyclomaticComplexMethod", "LoopWithTooManyJumpStatements")
    fun run() = runBlocking {
        while (true) {
            val (kind, payload) = readMessage(input) ?: break
            when (kind) {
                KIND_BYTES -> apply(engine.onInbound(payload))
                KIND_COMMAND -> onCommand(payload.decodeToString())
                else -> System.err.println("loopback: unknown kind $kind")
            }
        }
    }

    private suspend fun onCommand(text: String) {
        when (text) {
            "push" -> {
                val event = handler.newEvent(++eventCounter)
                apply(engine.sendApplication(RecordType.PUSH_EVENT.wire, event))
            }
            "reset" -> {
                engine.teardown()
                output.closed(currentPrefix)
                currentPrefix = null
                handler.reset()
                engine = newEngine(phone, trustRecord, handler)
            }
            else -> System.err.println("loopback: unknown command")
        }
    }

    private suspend fun apply(effects: List<SessionEffect>) {
        for (effect in effects) {
            when (effect) {
                is SessionEffect.Send -> {
                    output.frame(effect.bytes)
                    effect.completionToken?.let { token -> apply(engine.onSendComplete(token, true)) }
                }
                is SessionEffect.Authenticated -> {
                    currentPrefix = effect.sessionIdPrefix.copyOf()
                    output.authenticated(classCode(effect.sessionClass), effect.sessionIdPrefix)
                }
                is SessionEffect.Deliver -> Unit
                is SessionEffect.Terminated -> System.err.println("loopback: terminated ${effect.reason}")
                SessionEffect.Close -> {
                    output.closed(currentPrefix)
                    currentPrefix = null
                }
            }
        }
    }
}

/**
 * OTP-10 enrollment/recovery loopback. The production pairing machine owns the window and the
 * enrollment connection; the same real trust store and activation engine drive the reconnect
 * that resumes an interrupted activation.
 */
private class PairingLoopback(
    seed: String,
    private val phone: LoopbackIdentity,
    private val input: DataInputStream,
    private val output: LoopbackOutput,
) {
    private val clock: () -> Long = { System.nanoTime() / 1_000_000L }
    private val random = SecureRandom()
    private val core = VelesPairingCore()
    private val proofs = ProofRegistry()
    private val storage = LoopbackTrustStorage(initialSnapshot())
    private val repository: InstallationTrustRepository = TrustRepository(
        storage,
        LoopbackIdentityKeyStore(toHex(phone.publicKey)),
        clock,
        { CONTINUITY_TOKEN },
        clock,
        ActivationVerifier { peer, step, proof -> proofs.consume(peer, step, proof) },
    )
    private val limits = PairingLimits()
    private val window = PairingWindow(clock, FixedPairingRandom())
    private val admission = PairingAdmission(limits, clock)
    private val handler: ApplicationHandler = ActivationEngine(repository, proofs)
    private var connId = 0L
    private var machine: PhonePairingMachine? = null
    private var session: PhoneSessionEngine? = null
    private var currentPrefix: ByteArray? = null

    @Suppress("CyclomaticComplexMethod", "LoopWithTooManyJumpStatements")
    fun run() = runBlocking {
        while (true) {
            val (kind, payload) = readMessage(input) ?: break
            when (kind) {
                KIND_BYTES -> onBytes(payload)
                KIND_COMMAND -> onCommand(payload.decodeToString())
                else -> System.err.println("loopback: unknown kind $kind")
            }
        }
        machine?.onDisconnect()
        session?.teardown()
        System.err.println("loopback: closed with ${core.openHandles()} open pairing handles")
    }

    private suspend fun onBytes(payload: ByteArray) {
        val active = machine
        if (active != null) {
            applyPairing(active.onFrame(payload))
            return
        }
        val current = session
        if (current == null) {
            System.err.println("loopback: no pairing machine or session for a frame")
            return
        }
        applySession(current, current.onInbound(payload))
    }

    private suspend fun onCommand(text: String) {
        when (text) {
            "pair" -> openWindow()
            "disconnect" -> {
                machine?.onDisconnect()
                machine = null
                output.state(PairingState.IDLE.name)
            }
            "reconnect" -> {
                machine?.onDisconnect()
                machine = null
                session?.teardown()
                currentPrefix = null
                session = newSessionEngine()
            }
            "state" -> output.state(machine?.state?.name ?: PairingState.IDLE.name)
            "trust" -> output.trust(trustJson())
            else -> System.err.println("loopback: unknown command")
        }
    }

    private fun openWindow() {
        if (window.isOpen()) window.cancel()
        val opened = window.open()
        output.window(opened.pairingId)
        machine = PhonePairingMachine(
            window = window,
            admission = admission,
            conn = ++connId,
            trust = repository,
            coreProvider = { core },
            clock = clock,
            limits = limits,
            nonce = { ByteArray(NONCE_BYTES).also(random::nextBytes) },
            label = { "Phone" },
            identityNonce = { ByteArray(NONCE_BYTES).also(random::nextBytes) },
            proofs = proofs,
        )
    }

    private fun applyPairing(outgoing: List<Outgoing>) {
        for (action in outgoing) {
            when (action) {
                is Outgoing.Frame -> output.frame(action.bytes)
                Outgoing.Disconnect -> output.closed(null)
            }
        }
    }

    private suspend fun applySession(engine: PhoneSessionEngine, effects: List<SessionEffect>) {
        for (effect in effects) {
            when (effect) {
                is SessionEffect.Send -> {
                    output.frame(effect.bytes)
                    effect.completionToken?.let { token -> applySession(engine, engine.onSendComplete(token, true)) }
                }
                is SessionEffect.Authenticated -> {
                    currentPrefix = effect.sessionIdPrefix.copyOf()
                    output.authenticated(classCode(effect.sessionClass), effect.sessionIdPrefix)
                    // The production host records lastSeen after the authenticated gate.
                    try {
                        repository.markAuthenticated(toHex(effect.peerId))
                    } catch (_: Exception) {
                        System.err.println("loopback: markAuthenticated refused")
                    }
                    for (record in effect.unprompted) {
                        applySession(engine, engine.sendApplication(record.type, record.body))
                    }
                }
                is SessionEffect.Deliver -> Unit
                is SessionEffect.Terminated -> System.err.println("loopback: terminated ${effect.reason}")
                SessionEffect.Close -> {
                    output.closed(currentPrefix)
                    currentPrefix = null
                }
            }
        }
    }

    private fun newSessionEngine(): PhoneSessionEngine = PhoneSessionEngine(
        localInstallationId = phone.installationId,
        signer = phone.signer,
        lookup = PeerLookup { id -> repositoryPeer(id) },
        clock = clock,
        nonce = { ByteArray(NONCE_BYTES).also(random::nextBytes) },
        handler = handler,
    )

    private suspend fun repositoryPeer(id: ByteArray): PeerRecord? {
        val peer = repository.peers().firstOrNull { fromHex(it.installationId).contentEquals(id) } ?: return null
        return PeerRecord(
            installationId = fromHex(peer.installationId),
            identityPublicKey = fromHex(peer.publicKey),
            state = localState(peer.state),
            et = fromHex(peer.enrollmentTranscript),
        )
    }

    private suspend fun trustJson(): String {
        val identity = repository.identity()
        val peers = repository.peers()
        return buildJsonObject {
            put(
                "identity",
                buildJsonObject {
                    put("installationId", identity.installationId)
                    put("publicKey", identity.publicKey)
                },
            )
            putJsonArray("peers") {
                for (peer in peers) {
                    addJsonObject {
                        put("installationId", peer.installationId)
                        put("publicKey", peer.publicKey)
                        put("label", peer.label)
                        put("deviceType", peer.deviceType)
                        put("enrollmentTranscript", peer.enrollmentTranscript)
                        put("fingerprint", peer.fingerprint)
                        put("state", peer.state.name.lowercase())
                    }
                }
            }
            put("openHandles", core.openHandles())
        }.toString()
    }

    private fun initialSnapshot(): TrustSnapshot = TrustSnapshot(
        InstallationIdentity(toHex(phone.installationId), toHex(phone.publicKey)),
        emptyList(),
        BOOT_MARKER,
        CONTINUITY_TOKEN,
    )
}

private fun localState(state: TrustState): LocalRecordState = when (state) {
    TrustState.PENDING -> LocalRecordState.PENDING
    TrustState.COMMITTED_WAITING_ACK -> LocalRecordState.COMMITTED_WAITING_ACK
    TrustState.ACTIVE -> LocalRecordState.ACTIVE
}

private fun classCode(value: SessionClass): Byte = when (value) {
    SessionClass.FULL -> 0x00
    SessionClass.FULL_PENDING_PEER -> 0x01
    SessionClass.ACTIVATION_RECOVERY -> 0x02
}.toByte()

private class LoopbackTrustStorage(initial: TrustSnapshot?) : TrustStorage {
    private var value: TrustSnapshot? = initial

    override suspend fun <T> transaction(block: (TrustSnapshot?) -> Pair<TrustSnapshot, T>): T {
        val (next, result) = block(value)
        value = next
        return result
    }
}

/** Owns the phone's installation key for the loopback; the seed derives it before this runs. */
private class LoopbackIdentityKeyStore(private val publicKeyHex: String) : IdentityKeyStore {
    override fun publicKey(): String? = publicKeyHex

    override fun generate(): String = publicKeyHex

    override fun sign(message: ByteArray): ByteArray = ByteArray(0)
}

/**
 * A fixed [SecureRandom] for the loopback window: every four-byte draw is the code value 42
 * (`000042`), every longer draw is a deterministic pairing id. Test-only.
 */
private class FixedPairingRandom : SecureRandom() {
    private var counter = 0

    override fun nextBytes(bytes: ByteArray) {
        if (bytes.size == CODE_DRAW_BYTES) {
            bytes.fill(0)
            bytes[bytes.lastIndex] = FIXED_CODE_VALUE.toByte()
            return
        }
        counter += 1
        bytes.fill(counter.toByte())
    }

    private companion object {
        const val CODE_DRAW_BYTES = 4
        const val FIXED_CODE_VALUE = 42
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
    nonce = { ByteArray(NONCE_BYTES).also { SecureRandom().nextBytes(it) } },
    handler = handler,
)

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

private fun toHex(value: ByteArray): String = value.joinToString("") { byte ->
    (byte.toInt() and BYTE_MASK).toString(HEX_RADIX).padStart(HEX_BYTE_CHARS, '0')
}

private fun fromHex(value: String): ByteArray {
    require(value.length % 2 == 0) { "malformed hex value" }
    return ByteArray(value.length / 2) { index ->
        value.substring(index * 2, index * 2 + 2).toInt(HEX_RADIX).toByte()
    }
}

private const val BYTE_MASK = 0xff
private const val HEX_RADIX = 16
private const val HEX_BYTE_CHARS = 2
