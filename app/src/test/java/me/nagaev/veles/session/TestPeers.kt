package me.nagaev.veles.session

import kotlinx.serialization.json.JsonObject
import java.math.BigInteger
import java.security.KeyPair
import java.security.PrivateKey
import java.security.Signature
import java.security.interfaces.ECPublicKey

/** Injected monotonic clock for the engine's deadline and liveness tests. */
internal class FakeClock(var now: Long = 1_000L) {
    fun advance(millis: Long) {
        now += millis
    }
}

/** A software P-256 identity: a JCA key pair, its SEC1 point and a DER signer. */
internal class TestIdentity private constructor(val pair: KeyPair) {
    val publicKey: ByteArray = TestPeers.uncompressed(pair.public as ECPublicKey)
    val signer: IdentitySigner = IdentitySigner { message -> TestPeers.signDer(pair.private, message) }

    fun signP1363(message: ByteArray): ByteArray = EcdsaP1363.normalizeLowS(EcdsaP1363.fromDer(TestPeers.signDer(pair.private, message)))

    companion object {
        fun generate(): TestIdentity = TestIdentity(P256.generate())
    }
}

/**
 * Deterministic peers for the engine tests: software P-256 identities, the committed
 * fixture's fixed ephemeral keys and nonces, and a client-side harness that drives the
 * phone engine through the §11.3 exchange.
 */
internal object TestPeers {
    private val input: JsonObject get() = SessionFixtures.input(SessionFixtures.RECONNECT)
    private val outputs: JsonObject get() = SessionFixtures.outputs(SessionFixtures.RECONNECT)

    val phoneInstallationId: ByteArray get() = SessionFixtures.bytes(input, "phone_installation_id")
    val clientInstallationId: ByteArray get() = SessionFixtures.bytes(input, "client_installation_id")
    val phoneNonce: ByteArray get() = SessionFixtures.bytes(input, "phone_nonce")
    val clientNonce: ByteArray get() = SessionFixtures.bytes(input, "client_nonce")
    val clientCaps: ByteArray get() = SessionFixtures.caps(input, "client_caps")
    val phoneEphemeral: KeyPair get() = fixedPair("phone_ephemeral_priv", "phone_ephemeral_pub")
    val clientEphemeral: KeyPair get() = fixedPair("client_ephemeral_priv", "client_ephemeral_pub")

    fun engine(
        signer: IdentitySigner,
        lookup: PeerLookup,
        clock: FakeClock,
        handler: ApplicationHandler? = null,
    ): PhoneSessionEngine = PhoneSessionEngine(
        localInstallationId = phoneInstallationId,
        signer = signer,
        lookup = lookup,
        clock = { clock.now },
        ephemeral = { phoneEphemeral },
        nonce = { phoneNonce },
        handler = handler,
    )

    fun lookup(record: PeerRecord?): PeerLookup = PeerLookup { installationId ->
        record?.takeIf { it.installationId.contentEquals(installationId) }
    }

    fun record(
        client: TestClient,
        state: LocalRecordState = LocalRecordState.ACTIVE,
    ): PeerRecord = PeerRecord(client.installationId, client.identity.publicKey, state)

    fun signDer(privateKey: PrivateKey, message: ByteArray): ByteArray = Signature.getInstance("SHA256withECDSA").run {
        initSign(privateKey)
        update(message)
        sign()
    }

    fun uncompressed(publicKey: ECPublicKey): ByteArray {
        val x = coordinate(publicKey.w.affineX)
        val y = coordinate(publicKey.w.affineY)
        return byteArrayOf(POINT_TAG.toByte()) + x + y
    }

    private fun coordinate(value: BigInteger): ByteArray {
        val raw = value.toByteArray()
        return when {
            raw.size == COORDINATE_BYTES -> raw
            raw.size > COORDINATE_BYTES -> raw.copyOfRange(raw.size - COORDINATE_BYTES, raw.size)
            else -> ByteArray(COORDINATE_BYTES - raw.size) + raw
        }
    }

    private fun fixedPair(privateKeyField: String, publicKeyField: String): KeyPair = KeyPair(
        P256.requireValidPoint(SessionFixtures.bytes(outputs, publicKeyField)),
        P256.privateFromScalar(SessionFixtures.bytes(input, privateKeyField)),
    )

    const val SESSION_PREFIX_BYTES = 8
    const val HASH_BYTES = 32
    private const val COORDINATE_BYTES = 32
    private const val POINT_TAG = 0x04
}

/** A trust lookup whose record can be deleted between handshake steps. */
internal class MutablePeerLookup(var record: PeerRecord?) : PeerLookup {
    override suspend fun find(installationId: ByteArray): PeerRecord? = record?.takeIf { it.installationId.contentEquals(installationId) }
}

/** The client half of the handshake, built directly on the Task 2 primitives. */
internal class TestClient(
    val identity: TestIdentity = TestIdentity.generate(),
    val installationId: ByteArray = TestPeers.clientInstallationId,
    private val ephemeral: KeyPair = TestPeers.clientEphemeral,
    private val nonce: ByteArray = TestPeers.clientNonce,
) {
    val helloPayload: ByteArray = ReconnectHello(
        installationId,
        TestPeers.uncompressed(ephemeral.public as ECPublicKey),
        nonce,
        Role.CLIENT,
        TestPeers.clientCaps.copyOf(),
    ).encode()
    val helloFrame: ByteArray = PreAuthCodec.encode(FrameType.RECONNECT_HELLO.wire, helloPayload)

    lateinit var phoneHello: ReconnectHello
    lateinit var rbase: ByteArray
    lateinit var signature: ByteArray
    lateinit var confirmInput: ByteArray
    lateinit var keys: ReconnectKeys

    /** Accepts the phone's hello and signature, then signs the transcript and derives keys. */
    fun acceptPhoneHello(phoneHelloPayload: ByteArray, phoneSignature: ByteArray) {
        phoneHello = ReconnectHello.decode(phoneHelloPayload, Role.PHONE)
        rbase = ReconnectKeySchedule.rbase(phoneHelloPayload, helloPayload)
        signature = identity.signP1363(rbase)
        confirmInput = ReconnectKeySchedule.confirmInput(rbase, phoneSignature, signature)
        keys = ReconnectKeySchedule.derive(
            P256.ecdhX(ephemeral.private, phoneHello.ephemeralPublicKey),
            phoneHello.nonce,
            nonce,
        )
    }

    fun signatureFrame(): ByteArray {
        val frame = SignatureFrame(Role.CLIENT, signature)
        return PreAuthCodec.encode(FrameType.RECONNECT_SIGNATURE.wire, frame.encode())
    }

    fun confirmRecord(peerState: Int = PEER_STATE_ACTIVE, confirmation: ByteArray? = null): ByteArray {
        val confirm = confirmation ?: ReconnectKeySchedule.confirmation(keys.clientConfirmKey, confirmInput)
        return RecordCodec.seal(
            keys.c2pKey,
            keys.c2pPrefix,
            keys.sessionId.copyOfRange(0, TestPeers.SESSION_PREFIX_BYTES),
            Direction.CLIENT_TO_PHONE,
            RecordType.RECONNECT_CONFIRM.wire,
            0uL,
            confirm + byteArrayOf(peerState.toByte()),
        )
    }

    fun heartbeatRecord(): ByteArray = RecordCodec.seal(
        keys.c2pKey,
        keys.c2pPrefix,
        keys.sessionId.copyOfRange(0, TestPeers.SESSION_PREFIX_BYTES),
        Direction.CLIENT_TO_PHONE,
        RecordType.APP_HEARTBEAT.wire,
        0uL,
        ByteArray(0),
    )

    companion object {
        const val PEER_STATE_ACTIVE = 0x01
        const val PEER_STATE_BELOW_ACTIVE = 0x02
    }
}

/** The result of driving a phone engine up to (but not including) the peer's confirm. */
internal class HandshakeProgress(
    val token: Long,
    val phoneConfirmRecord: ByteArray,
    val clientConfirmRecord: ByteArray,
    val phoneHelloPayload: ByteArray,
    val phoneSignature: ByteArray,
)

/** Client hello → phone hello + signature → client signature → phone confirm. */
internal suspend fun PhoneSessionEngine.driveToPeerConfirm(
    client: TestClient,
    peerState: Int = TestClient.PEER_STATE_ACTIVE,
): HandshakeProgress {
    val helloEffects = onInbound(client.helloFrame)
    check(helloEffects.size == 2 && helloEffects.all { it is SessionEffect.Send }) {
        "expected phone hello and signature"
    }
    val phoneHelloPayload = PreAuthCodec.decode((helloEffects[0] as SessionEffect.Send).bytes).second
    val phoneSignature = SignatureFrame.decode(
        PreAuthCodec.decode((helloEffects[1] as SessionEffect.Send).bytes).second,
        Role.PHONE,
    ).signature
    client.acceptPhoneHello(phoneHelloPayload, phoneSignature)
    val confirmEffects = onInbound(client.signatureFrame())
    check(confirmEffects.size == 1 && confirmEffects[0] is SessionEffect.Send) {
        "expected the phone reconnect_confirm"
    }
    val confirm = confirmEffects[0] as SessionEffect.Send
    return HandshakeProgress(
        token = checkNotNull(confirm.completionToken),
        phoneConfirmRecord = confirm.bytes,
        clientConfirmRecord = client.confirmRecord(peerState),
        phoneHelloPayload = phoneHelloPayload,
        phoneSignature = phoneSignature,
    )
}

/** Drives the full handshake and returns the effects of the send completion. */
internal suspend fun PhoneSessionEngine.completeHandshake(
    client: TestClient,
    peerState: Int = TestClient.PEER_STATE_ACTIVE,
): List<SessionEffect> {
    val progress = driveToPeerConfirm(client, peerState)
    onInbound(progress.clientConfirmRecord)
    return onSendComplete(progress.token, true)
}
