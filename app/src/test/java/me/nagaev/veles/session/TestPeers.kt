package me.nagaev.veles.session

import kotlinx.serialization.json.JsonObject
import java.math.BigInteger
import java.nio.ByteBuffer
import java.security.KeyPair
import java.security.PrivateKey
import java.security.Signature
import java.security.interfaces.ECPublicKey
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

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

/**
 * Captures the session context the engine hands its handler at the authentication gate
 * (OTP-10 plan 9b) and delegates the record surface to an optional handler.
 */
internal class ContextCapturingHandler(
    private val delegate: ApplicationHandler? = null,
) : ApplicationHandler {
    var context: SessionContext? = null
        private set

    override fun handles(type: Int): Boolean = delegate?.handles(type) ?: false

    override suspend fun handle(
        type: Int,
        body: ByteArray,
        reply: suspend (type: Int, body: ByteArray) -> Unit,
    ) {
        delegate?.handle(type, body, reply)
    }

    override fun forSession(context: SessionContext): ApplicationHandler {
        this.context = context
        return this
    }
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

    /** A client-to-phone protected record at [sequence] with the session's traffic keys. */
    fun record(type: Int, sequence: ULong, body: ByteArray = ByteArray(0)): ByteArray = RecordCodec.seal(
        keys.c2pKey,
        keys.c2pPrefix,
        sessionPrefix(),
        Direction.CLIENT_TO_PHONE,
        type,
        sequence,
        body,
    )

    /** A phone-to-client protected record, used for direction-authorization negatives. */
    fun phoneRecord(type: Int, sequence: ULong, body: ByteArray = ByteArray(0)): ByteArray = RecordCodec.seal(
        keys.p2cKey,
        keys.p2cPrefix,
        sessionPrefix(),
        Direction.PHONE_TO_CLIENT,
        type,
        sequence,
        body,
    )

    /**
     * A well-formed GCM record whose type is outside §7.3, built without [RecordCodec.seal]'s
     * type check so the engine's own authorization can be exercised.
     */
    fun uncheckedRecord(type: Int, sequence: ULong, body: ByteArray = ByteArray(0)): ByteArray {
        val header = ByteArray(RecordCodec.HEADER)
        header[0] = PROTOCOL_VERSION.toByte()
        header[1] = SCHEMA_VERSION.toByte()
        header[2] = Direction.CLIENT_TO_PHONE.wire.toByte()
        header[3] = (type ushr BITS_PER_BYTE).toByte()
        header[4] = type.toByte()
        ByteBuffer.wrap(header).putLong(SEQUENCE_OFFSET, sequence.toLong())
        header[LENGTH_OFFSET] = (body.size ushr BITS_PER_BYTE).toByte()
        header[LENGTH_OFFSET + 1] = body.size.toByte()
        sessionPrefix().copyInto(header, SESSION_OFFSET)
        val nonce = keys.c2pPrefix + ByteBuffer.allocate(LONG_BYTES).putLong(sequence.toLong()).array()
        val cipher = Cipher.getInstance(AES_TRANSFORMATION)
        cipher.init(
            Cipher.ENCRYPT_MODE,
            SecretKeySpec(keys.c2pKey, AES_ALGORITHM),
            GCMParameterSpec(GCM_TAG_BITS, nonce),
        )
        cipher.updateAAD(header)
        return header + cipher.doFinal(body)
    }

    private fun sessionPrefix(): ByteArray = keys.sessionId.copyOfRange(0, TestPeers.SESSION_PREFIX_BYTES)

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
        private const val PROTOCOL_VERSION = 1
        private const val SCHEMA_VERSION = 1
        private const val BITS_PER_BYTE = 8
        private const val SEQUENCE_OFFSET = 5
        private const val LENGTH_OFFSET = 13
        private const val SESSION_OFFSET = 15
        private const val LONG_BYTES = 8
        private const val GCM_TAG_BITS = 128
        private const val AES_TRANSFORMATION = "AES/GCM/NoPadding"
        private const val AES_ALGORITHM = "AES"
    }
}

/** OTP2-ENC-021 `app_error` body: code u8 || reason_len u16be || reason UTF-8. */
internal fun appErrorBody(code: AppErrorCode, reason: ByteArray = ByteArray(0)): ByteArray {
    require(reason.size <= APP_ERROR_REASON_MAX) { "app_error reason is bounded to 64 bytes" }
    return byteArrayOf(code.wire.toByte(), (reason.size ushr 8).toByte(), reason.size.toByte()) + reason
}

/** Opens a phone-sealed record with the client half of the session keys. */
internal fun TestClient.openPhoneRecord(record: ByteArray, sequence: ULong): OpenedRecord = RecordCodec.open(
    keys.p2cKey,
    keys.p2cPrefix,
    keys.sessionId.copyOfRange(0, TestPeers.SESSION_PREFIX_BYTES),
    Direction.PHONE_TO_CLIENT,
    sequence,
    record,
)

/** Asserts the canonical `[Terminated, Close]` terminal pair. */
internal fun assertTerminated(effects: List<SessionEffect>) {
    org.junit.Assert.assertEquals("terminal effects", 2, effects.size)
    org.junit.Assert.assertTrue("first effect is Terminated", effects[0] is SessionEffect.Terminated)
    org.junit.Assert.assertTrue("reason is non-empty", (effects[0] as SessionEffect.Terminated).reason.isNotEmpty())
    org.junit.Assert.assertEquals(SessionEffect.Close, effects[1])
    org.junit.Assert.assertTrue("nothing delivered", effects.none { it is SessionEffect.Deliver })
}

/** Asserts the canonical `[preauth_reject, Close]` pre-auth pair. */
internal fun assertPreAuthReject(effects: List<SessionEffect>) {
    org.junit.Assert.assertEquals("pre-auth terminal effects", 2, effects.size)
    val send = effects[0] as SessionEffect.Send
    org.junit.Assert.assertArrayEquals(byteArrayOf(0x0F, 0, 0), send.bytes)
    org.junit.Assert.assertEquals(SessionEffect.Close, effects[1])
}

private const val APP_ERROR_REASON_MAX = 64

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
