package me.nagaev.veles.pairing

import androidx.test.ext.junit.runners.AndroidJUnit4
import kotlinx.coroutines.runBlocking
import me.nagaev.veles.session.Direction
import me.nagaev.veles.session.P256
import me.nagaev.veles.session.RecordCodec
import me.nagaev.veles.session.RecordType
import me.nagaev.veles.session.Role
import me.nagaev.veles.trust.ActivationStep
import me.nagaev.veles.trust.ActivationVerifier
import me.nagaev.veles.trust.IdentityKeyStore
import me.nagaev.veles.trust.InstallationIdentity
import me.nagaev.veles.trust.InstallationTrustRepository
import me.nagaev.veles.trust.PendingPeer
import me.nagaev.veles.trust.TrustRepository
import me.nagaev.veles.trust.TrustSnapshot
import me.nagaev.veles.trust.TrustState
import me.nagaev.veles.trust.TrustStorage
import me.nagaev.veles.trust.TrustedPeer
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.math.BigInteger
import java.security.KeyPair
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.interfaces.ECPublicKey
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * OTP-10 plan 7.2: the production [PhonePairingMachine] over the device `veles_crypto` core,
 * driven by an independent Kotlin client that runs SPAKE2 party B on the same core.
 *
 * The test proves the full enrollment on a real device: the fixed window code `000042`
 * verifies both confirmations, the phone persists a complete pending record before the
 * prepare, the client's verified commit and ack activate that record, and both trust stores
 * end with the same installation ids, public keys, `ET` and fingerprint. Every core handle is
 * closed at the end. It also proves a wrong confirmation is rejected with the generic
 * `preauth_reject` and leaves no record.
 */
@RunWith(AndroidJUnit4::class)
class PairingInstrumentedTest {
    @Test
    fun enrollment_activates_matching_records_on_the_device_core() = runBlocking {
        val harness = Harness()
        try {
            val run = EnrollmentRun(harness)
            run.enroll()
            val phoneRecord = harness.repository.peers().single()
            assertEquals(TrustState.ACTIVE, phoneRecord.state)
            assertEquals(hex(harness.client.installationId), phoneRecord.installationId)
            assertEquals(hex(harness.client.publicKey), phoneRecord.publicKey)
            assertEquals(PairingState.ACTIVE, harness.machine.state)

            val clientRecord = harness.clientTrust.peers.single()
            assertEquals(TrustState.PENDING, clientRecord.state)
            assertEquals(hex(harness.phone.installationId), clientRecord.installationId)
            assertEquals(hex(harness.phone.publicKey), clientRecord.publicKey)
            assertEquals(phoneRecord.enrollmentTranscript, clientRecord.enrollmentTranscript)
            assertEquals(phoneRecord.fingerprint, clientRecord.fingerprint)
            assertEquals(
                hex(EnrollmentRecords.pairFingerprint(harness.phone.publicKey, harness.client.publicKey)),
                phoneRecord.fingerprint,
            )
            assertEquals(0, harness.core.openHandles())
        } finally {
            harness.close()
        }
    }

    @Test
    fun wrong_confirmation_is_generic_and_persists_nothing() = runBlocking {
        val harness = Harness()
        try {
            val run = EnrollmentRun(harness)
            run.openAndExchange()
            val outgoing = run.sendConfirmation(ByteArray(CONFIRMATION_BYTES))
            run.close()
            assertEquals(1, outgoing.filterIsInstance<Outgoing.Frame>().size)
            assertArrayEquals(
                PairingFrames.preauthReject(),
                outgoing.filterIsInstance<Outgoing.Frame>().single().bytes,
            )
            assertTrue(outgoing.last() is Outgoing.Disconnect)
            assertTrue(harness.repository.peers().isEmpty())
            assertFalse(run.hasLiveExecution())
            assertEquals(0, harness.core.openHandles())
        } finally {
            harness.close()
        }
    }
}

/** The phone's production trust repository over an in-memory store and the device core. */
private class Harness {
    var now = 1_000L
    val core = VelesPairingCore()
    val phone = IdentityMaterial("instrumented-phone")
    val client = IdentityMaterial("instrumented-client")
    val proofs = ProofRegistry()
    private val storage = MemoryStorage(
        TrustSnapshot(
            InstallationIdentity(hex(phone.installationId), hex(phone.publicKey)),
            emptyList(),
            "instrumented-boot",
            CONTINUITY_TOKEN,
        ),
    )
    val repository: InstallationTrustRepository = TrustRepository(
        storage,
        FixedKeyStore(hex(phone.publicKey)),
        { now },
        { CONTINUITY_TOKEN },
        { now },
        ActivationVerifier { peer, step, proof -> proofs.consume(peer, step, proof) },
    )
    val clientTrust = ClientTrust(client)
    val window = PairingWindow({ now }, FixedRandom())
    val admission = PairingAdmission(PairingLimits(), { now })
    val machine: PhonePairingMachine

    init {
        window.open()
        machine = PhonePairingMachine(
            window = window,
            admission = admission,
            conn = 1L,
            trust = repository,
            coreProvider = { core },
            clock = { now },
            limits = PairingLimits(),
            nonce = { ByteArray(NONCE_BYTES) { index -> (index + 1).toByte() } },
            label = { PHONE_LABEL },
            identityNonce = { ByteArray(NONCE_BYTES) { index -> (0x40 + index).toByte() } },
            proofs = proofs,
        )
    }

    suspend fun close() {
        machine.onDisconnect()
    }
}

/** The client's half of one enrollment connection, on the device core. */
private class EnrollmentRun(private val harness: Harness) {
    private val client = harness.client
    private val clientNonce = ByteArray(NONCE_BYTES) { index -> (0xA0 + index).toByte() }
    private val clientCaps = byteArrayOf(0x01, 0x03)
    private var execution: PairingExecution? = null
    private var keys: EnrollmentKeys? = null
    private var prefix: ByteArray? = null
    private var context: ByteArray? = null
    private var phonePoint: ByteArray? = null
    private var clientPoint: ByteArray? = null
    private var phoneConfirm: ByteArray? = null
    private var clientConfirm: ByteArray? = null
    private var phoneBody: ByteArray? = null
    private var clientBody: ByteArray? = null
    private var transcript: ByteArray? = null
    private var txSequence = 0uL
    private var rxSequence = 0uL

    fun hasLiveExecution(): Boolean = execution?.isClosed == false

    fun close() {
        execution?.close()
        execution = null
        keys?.wipe()
        keys = null
    }

    fun enroll() = runBlocking {
        val offer = openAndExchange()
        acceptConfirmation(offer)
        val phoneIdentity = readPhoneIdentity()
        val et = releaseClientIdentity(phoneIdentity)
        val prepare = harness.machine.onFrame(sealed(RecordType.ENROLL_IDENTITY.wire, clientBody!!))
        verifyPrepare(prepare.singleProtectedRecord())
        val complete = harness.machine.onFrame(sealed(RecordType.ACTIVATION_COMMIT.wire, activationMac(clientKeys().clientActivationKey, et, STAGE_COMMIT)))
        verifyComplete(complete.singleProtectedRecord(), et)
        val ack = activationMac(clientKeys().clientActivationKey, et, STAGE_ACK)
        // OTP2-STA-010b step 6: the verified ack activates the record and ends the connection.
        val closing = harness.machine.onFrame(sealed(RecordType.ACTIVATION_ACK.wire, ack))
        assertEquals(listOf<Outgoing>(Outgoing.Disconnect), closing)
        assertEquals(PairingState.ACTIVE, harness.machine.state)
        keys?.wipe()
        execution?.close()
        execution = null
    }

    /** Sends `pairing_request`, starts party B and returns the phone's parsed `pairing_offer`. */
    suspend fun openAndExchange(): Offer {
        val request = PairingFrames.encode(PairingFrameType.REQUEST, requestPayload())
        val offer = parseOffer(harness.machine.onFrame(request).singleFrame(type = PairingFrameType.PA))
        val ctx = contextOf(offer, clientNonce, clientCaps)
        context = ctx
        phonePoint = offer.point.copyOf()
        val started = harness.core.start(Role.CLIENT, CODE.toByteArray(Charsets.US_ASCII), ctx)
        execution = PairingExecution(harness.core, started.handle)
        clientPoint = started.point.copyOf()
        execution?.exchange(offer.point)
        val outgoing = harness.machine.onFrame(PairingFrames.encode(PairingFrameType.PB, started.point))
        phoneConfirm = outgoing.singleFrame(type = PairingFrameType.CA)
        return offer
    }

    /** Verifies `cA` and releases `cB`, leaving the phone waiting for the client identity. */
    suspend fun acceptConfirmation(offer: Offer) {
        val current = execution ?: error("no live execution")
        val confirmation = current.confirm(phoneConfirm ?: error("no cA"))
        clientConfirm = confirmation.copyOf()
        val shared = current.finish()
        execution = null
        keys = EnrollmentSchedule.derive(shared, context ?: error("no context"))
        shared.fill(0)
        prefix = EnrollmentSchedule.sessionIdPrefix(context ?: error("no context"))
        val outgoing = harness.machine.onFrame(
            PairingFrames.encode(PairingFrameType.CB, confirmation),
        )
        // The phone's own enroll_identity is the first protected record.
        if (outgoing.isEmpty()) error("the phone sent no identity")
        phoneIdentityFrame = outgoing.filterIsInstance<Outgoing.Frame>().single().bytes
    }

    suspend fun sendConfirmation(confirmation: ByteArray): List<Outgoing> = harness.machine.onFrame(
        PairingFrames.encode(PairingFrameType.CB, confirmation),
    )

    private suspend fun readPhoneIdentity(): IdentityRecord {
        val frame = phoneIdentityFrame ?: error("no phone identity frame")
        val body = openPhoneRecord(frame, RecordType.ENROLL_IDENTITY)
        val record = EnrollmentRecords.decodeForStorage(
            body,
            IdentityRole.PHONE,
            { point -> P256.requireValidPoint(point) },
            { },
        )
        phoneBody = EnrollmentRecords.encode(record, IdentityRole.PHONE)
        return record
    }

    private suspend fun releaseClientIdentity(phone: IdentityRecord): ByteArray {
        val own = EnrollmentRecords.create(
            client.installationId,
            CLIENT_LABEL,
            DeviceType.DESKTOP,
            client.publicKey,
            ByteArray(NONCE_BYTES) { index -> (0x60 + index).toByte() },
        )
        clientBody = EnrollmentRecords.encode(own, IdentityRole.CLIENT)
        val et = EnrollmentRecords.transcript(
            context ?: error("no context"),
            phonePoint ?: error("no pA"),
            clientPoint ?: error("no pB"),
            phoneConfirm ?: error("no cA"),
            clientConfirm ?: error("no cB"),
            phoneBody ?: error("no phone body"),
            clientBody ?: error("no client body"),
        )
        transcript = et
        harness.clientTrust.addPending(
            PendingPeer(
                installationId = hex(phone.installationId),
                publicKey = hex(phone.identityPubkey),
                label = phone.label,
                deviceType = phone.deviceType.wire,
                enrollmentTranscript = hex(et),
            ),
        )
        return et
    }

    private suspend fun verifyPrepare(frame: ByteArray) {
        val opened = openPhoneRecord(frame, RecordType.ENROLLMENT_PREPARE)
        val expected = activationMac(clientKeys().phoneActivationKey, transcript ?: error("no ET"), STAGE_PREPARE)
        assertArrayEquals(expected, opened)
    }

    private suspend fun verifyComplete(frame: ByteArray, et: ByteArray) {
        val opened = openPhoneRecord(frame, RecordType.ACTIVATION_COMPLETE)
        val expected = activationMac(clientKeys().phoneActivationKey, et, STAGE_COMPLETE)
        assertArrayEquals(expected, opened)
    }

    /** Opens one phone→client protected record and asserts its §7 type. */
    private suspend fun openPhoneRecord(frame: ByteArray, type: RecordType): ByteArray {
        val opened = RecordCodec.open(
            clientKeys().aToBAesKey,
            clientKeys().aToBNoncePrefix,
            prefix ?: error("no prefix"),
            Direction.PHONE_TO_CLIENT,
            rxSequence,
            frame,
        )
        rxSequence += 1uL
        assertEquals(type.wire, opened.type)
        return opened.body
    }

    private fun sealed(type: Int, body: ByteArray): ByteArray = RecordCodec.seal(
        clientKeys().bToAAesKey,
        clientKeys().bToANoncePrefix,
        prefix ?: error("no prefix"),
        Direction.CLIENT_TO_PHONE,
        type,
        txSequence.also { txSequence += 1uL },
        body,
    )

    private fun clientKeys(): EnrollmentKeys = keys ?: error("no enrollment keys")

    private fun requestPayload(): ByteArray = clientNonce +
        byteArrayOf(0, 1, 0, clientCaps.size.toByte()) +
        clientCaps

    private fun contextOf(offer: Offer, nonce: ByteArray, caps: ByteArray): ByteArray {
        val prefixBytes = CONTEXT_PREFIX.toByteArray(Charsets.US_ASCII)
        val context = ByteArray(
            prefixBytes.size + 6 + PAIRING_ID_BYTES + 2 + NONCE_BYTES * 2 + 4 + offer.caps.size + caps.size,
        )
        var offset = 0
        prefixBytes.copyInto(context, offset)
        offset += prefixBytes.size
        offset = putU16(context, offset, 1)
        offset = putU16(context, offset, 1)
        offset = putU16(context, offset, 1)
        offer.pairingId.copyInto(context, offset)
        offset += PAIRING_ID_BYTES
        context[offset] = 0x01
        context[offset + 1] = 0x02
        offset += 2
        offer.phoneNonce.copyInto(context, offset)
        offset += NONCE_BYTES
        nonce.copyInto(context, offset)
        offset += NONCE_BYTES
        offset = putU16(context, offset, offer.caps.size)
        offer.caps.copyInto(context, offset)
        offset += offer.caps.size
        putU16(context, offset, caps.size)
        caps.copyInto(context, offset + 2)
        return context
    }

    private fun activationMac(key: ByteArray, et: ByteArray, stage: Byte): ByteArray = Mac.getInstance("HmacSHA256").run {
        init(SecretKeySpec(key, "HmacSHA256"))
        doFinal(et + byteArrayOf(stage))
    }

    private var phoneIdentityFrame: ByteArray? = null
}

private class Offer(
    val pairingId: ByteArray,
    val phoneNonce: ByteArray,
    val caps: ByteArray,
    val point: ByteArray,
)

private fun List<Outgoing>.singleFrame(type: PairingFrameType? = null): ByteArray {
    val frame = filterIsInstance<Outgoing.Frame>().single()
    val decoded = PairingFrames.decode(frame.bytes)
    if (type != null) assertEquals(type, decoded.type)
    return decoded.payload
}

/**
 * The one protected record the phone sent, still sealed. Post-confirmation traffic is §7
 * protected records, not `0x01`-`0x05` pairing frames, so it must be opened with
 * `RecordCodec`, never decoded with `PairingFrames.decode`.
 */
private fun List<Outgoing>.singleProtectedRecord(): ByteArray = filterIsInstance<Outgoing.Frame>().single().bytes

private fun parseOffer(payload: ByteArray): Offer {
    val fixed = PAIRING_ID_BYTES + NONCE_BYTES + 4
    require(payload.size >= fixed + POINT_BYTES) { "short pairing offer" }
    val capsLength = u16(payload, PAIRING_ID_BYTES + NONCE_BYTES + 2)
    require(u16(payload, PAIRING_ID_BYTES + NONCE_BYTES) == 1) { "unsupported offer version" }
    require(payload.size == fixed + capsLength + POINT_BYTES) { "malformed pairing offer" }
    return Offer(
        payload.copyOfRange(0, PAIRING_ID_BYTES),
        payload.copyOfRange(PAIRING_ID_BYTES, PAIRING_ID_BYTES + NONCE_BYTES),
        payload.copyOfRange(fixed, fixed + capsLength),
        payload.copyOfRange(fixed + capsLength, payload.size),
    )
}

private class IdentityMaterial(seed: String) {
    val pair: KeyPair = P256.generate()
    val installationId: ByteArray = MessageDigest.getInstance("SHA-256")
        .digest(seed.toByteArray())
        .copyOfRange(0, INSTALLATION_BYTES)
    val publicKey: ByteArray = uncompressed(pair.public as ECPublicKey)
}

/** The client's in-memory pending record; the enrollment connection never activates it. */
private class ClientTrust(private val identity: IdentityMaterial) : InstallationTrustRepository {
    val peers = mutableListOf<TrustedPeer>()

    override suspend fun identity(): InstallationIdentity = InstallationIdentity(hex(identity.installationId), hex(identity.publicKey))

    override suspend fun peers(): List<TrustedPeer> = peers.toList()

    override suspend fun activePeers(): List<TrustedPeer> = peers.filter { it.state == TrustState.ACTIVE }

    override suspend fun addPending(peer: PendingPeer) {
        val fingerprint = EnrollmentRecords.pairFingerprint(
            fromHex(peer.publicKey),
            identity.publicKey,
        )
        peers += TrustedPeer(
            peer.installationId,
            peer.publicKey,
            peer.label,
            peer.deviceType,
            peer.enrollmentTranscript,
            hex(fingerprint),
            TrustState.PENDING,
            0,
            null,
            0,
            CONTINUITY_TOKEN,
            0,
        )
    }

    override suspend fun confirm(peerId: String, step: ActivationStep, proof: ByteArray) {
        peers.replaceAll { peer ->
            if (peer.installationId == peerId && peer.state == TrustState.PENDING) {
                peer.copy(state = TrustState.ACTIVE)
            } else {
                peer
            }
        }
    }

    override suspend fun revoke(peerId: String) {
        peers.removeAll { it.installationId == peerId }
    }

    override suspend fun markAuthenticated(peerId: String) = Unit
}

private class MemoryStorage(initial: TrustSnapshot) : TrustStorage {
    private var value: TrustSnapshot? = initial

    override suspend fun <T> transaction(block: (TrustSnapshot?) -> Pair<TrustSnapshot, T>): T {
        val (next, result) = block(value)
        value = next
        return result
    }
}

private class FixedKeyStore(private val publicKeyHex: String) : IdentityKeyStore {
    override fun publicKey(): String? = publicKeyHex

    override fun generate(): String = publicKeyHex

    override fun sign(message: ByteArray): ByteArray = ByteArray(0)
}

/** Every four-byte draw is the code value 42 (`000042`); longer draws are deterministic ids. */
private class FixedRandom : SecureRandom() {
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

private fun uncompressed(key: ECPublicKey): ByteArray = byteArrayOf(0x04) +
    coordinate(key.w.affineX) +
    coordinate(key.w.affineY)

private fun coordinate(value: BigInteger): ByteArray {
    val raw = value.toByteArray()
    return when {
        raw.size == COORDINATE_BYTES -> raw
        raw.size > COORDINATE_BYTES -> raw.copyOfRange(raw.size - COORDINATE_BYTES, raw.size)
        else -> ByteArray(COORDINATE_BYTES - raw.size) + raw
    }
}

private fun u16(value: ByteArray, offset: Int): Int = ((value[offset].toInt() and U8_MASK) shl 8) or (value[offset + 1].toInt() and U8_MASK)

private fun putU16(target: ByteArray, offset: Int, value: Int): Int {
    target[offset] = (value ushr 8).toByte()
    target[offset + 1] = value.toByte()
    return offset + 2
}

private fun hex(value: ByteArray): String = value.joinToString("") { byte ->
    (byte.toInt() and U8_MASK).toString(16).padStart(2, '0')
}

private fun fromHex(value: String): ByteArray {
    require(value.length % 2 == 0) { "malformed hex value" }
    return ByteArray(value.length / 2) { index ->
        value.substring(index * 2, index * 2 + 2).toInt(16).toByte()
    }
}

private const val CODE = "000042"
private const val PHONE_LABEL = "Phone"
private const val CLIENT_LABEL = "Desktop"
private const val CONTEXT_PREFIX = "Veles SPAKE2 public context v1"
private const val CONTINUITY_TOKEN = "abababababababababababababababababababababababababababababababab"
private const val INSTALLATION_BYTES = 16
private const val PAIRING_ID_BYTES = 16
private const val NONCE_BYTES = 16
private const val POINT_BYTES = 65
private const val CONFIRMATION_BYTES = 32
private const val COORDINATE_BYTES = 32
private const val U8_MASK = 0xff
private const val STAGE_PREPARE: Byte = 0x00
private const val STAGE_COMMIT: Byte = 0x01
private const val STAGE_COMPLETE: Byte = 0x02
private const val STAGE_ACK: Byte = 0x03
