package me.nagaev.veles.pairing

import kotlinx.coroutines.runBlocking
import me.nagaev.veles.session.AppErrorCode
import me.nagaev.veles.session.Direction
import me.nagaev.veles.session.P256
import me.nagaev.veles.session.RecordCodec
import me.nagaev.veles.session.RecordType
import me.nagaev.veles.session.Role
import me.nagaev.veles.session.TestPeers
import me.nagaev.veles.trust.ActivationStep
import me.nagaev.veles.trust.ActivationVerifier
import me.nagaev.veles.trust.IdentityKeyStore
import me.nagaev.veles.trust.InstallationIdentity
import me.nagaev.veles.trust.InstallationTrustRepository
import me.nagaev.veles.trust.PendingPeer
import me.nagaev.veles.trust.TrustEncoding
import me.nagaev.veles.trust.TrustRepository
import me.nagaev.veles.trust.TrustSnapshot
import me.nagaev.veles.trust.TrustState
import me.nagaev.veles.trust.TrustStorage
import me.nagaev.veles.trust.TrustedPeer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.nio.ByteBuffer
import java.security.MessageDigest
import java.security.interfaces.ECPublicKey
import javax.crypto.Cipher
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * §11.1 `awaiting_cB` → `enrollment_protected` → `awaiting_commit`, the persist-first
 * `enrollment_prepare` gate, the OTP2-STA-012 duplicate refusal and cancel/erasure.
 *
 * The core is the deterministic [EnrollmentCore]: it verifies any 32-byte confirmation and
 * returns a fixed 16-byte `Ke`, so the test can derive the same enrollment schedule as the
 * phone and speak the protected record layer to it. The real core's `Ke` and enrollment
 * vectors stay with `EnrollmentScenarioTest` and `EnrollmentVectorsTest`.
 */
class EnrollmentFlowTest : PairingTestBase() {
    private val fake = EnrollmentCore()
    override val core: PairingCore = fake

    @Test
    fun phone_sends_record_only_after_cB_verified() = runBlocking {
        val h = Harness()
        try {
            h.offer()
            assertEquals("the point answer is cA", CA_TYPE, frameType(h.point()))
            assertEquals(PairingState.AWAITING_CB, h.machine.state)
            assertTrue("no enrollment record exists before a verified cB", h.trust.peers().isEmpty())
            assertEquals("no key schedule is derived before cB", 0, fake.count("finish"))

            val outgoing = h.confirm()

            assertEquals(PairingState.ENROLLMENT_PROTECTED, h.machine.state)
            val record = singleRecord(outgoing)
            assertEquals("the first protected record is enroll_identity", IDENTITY_TYPE, recordType(record))
            assertEquals("it is phone→client", PHONE_TO_CLIENT, direction(record))
            assertEquals("one key schedule is derived after cB", 1, fake.count("finish"))
            assertEquals("no trust record is written before the client identity", 0, h.trust.addPendingCalls)
            assertTrue("the one-use code is consumed", h.codeGone())
        } finally {
            h.close()
        }
    }

    @Test
    fun duplicate_installation_id_sends_enrollment_rejected_and_closes_window() = runBlocking {
        val h = Harness()
        try {
            h.seedActive(h.clientInstallationHex)
            h.runToCb()
            val existing = h.trust.peers().single()

            val outgoing = h.sendClientIdentity()

            assertEquals("the duplicate refusal is a protected app_error then a close", 2, outgoing.size)
            val error = singleRecord(outgoing)
            assertEquals("the protected refusal is app_error", ERROR_TYPE, recordType(error))
            val opened = h.openPhoneRecord(error, sequence = 1u)
            assertEquals("the error code is enrollment_rejected", AppErrorCode.ENROLLMENT_REJECTED.wire, opened[0].toInt() and U8_MASK)
            assertTrue("the duplicate check ran before addPending", h.trust.addPendingCalls == 0)
            assertTrue("no prepare was sent", !sawRecord(outputsOf(outgoing), PREPARE_TYPE))
            assertEquals("the machine returns to idle", PairingState.IDLE, h.machine.state)
            assertFalse("the window is closed", h.window.isOpen())
            assertEquals("the existing record is unchanged", existing, h.trust.peers().single())
            assertTrue("the existing record is still active", h.trust.peers().single().state == TrustState.ACTIVE)
        } finally {
            h.close()
        }
    }

    @Test
    fun pending_persisted_before_prepare() = runBlocking {
        val h = Harness()
        try {
            h.runToCb()
            val clientBody = h.clientIdentityBody()
            val expectedEt = h.expectedTranscript(clientBody)

            val outgoing = h.sendClientIdentity()

            val prepare = singleRecord(outgoing)
            assertEquals("the post-persist frame is enrollment_prepare", PREPARE_TYPE, recordType(prepare))
            assertEquals("the machine reached awaiting_commit", PairingState.AWAITING_COMMIT, h.machine.state)
            assertEquals("one complete pending record is persisted", 1, h.trust.addPendingCalls)
            val peer = h.trust.peers().single()
            assertEquals(TrustState.PENDING, peer.state)
            assertEquals("the record stores the canonical transcript", TrustEncoding.hex(expectedEt), peer.enrollmentTranscript)
            assertEquals("the record carries the client installation id", TrustEncoding.hex(CLIENT_INSTALLATION), peer.installationId)
            assertEquals("the record carries the client identity key", h.clientPublicKeyHex(), peer.publicKey)

            val body = h.openPhoneRecord(prepare, sequence = 1u)
            assertEquals("the prepare body is a 32-byte MAC", 32, body.size)
            assertTrue(
                "the prepare MAC is HMAC(phone activation key, ET || 0x00)",
                MessageDigest.isEqual(expectedPrepareMac(h.clientKeys.phoneActivationKey, expectedEt), body),
            )
        } finally {
            h.close()
        }
    }

    @Test
    fun persist_failure_publishes_nothing() = runBlocking {
        val h = Harness(failPersist = true)
        try {
            h.runToCb()

            val outgoing = h.sendClientIdentity()

            assertEquals("a failed persist is the generic failure plus a close", 2, outgoing.size)
            assertEquals("it is preauth_reject", PREAUTH_REJECT_TYPE, frameType(outgoing))
            assertTrue("no prepare frame crossed the wire", !sawRecord(outputsOf(outgoing), PREPARE_TYPE))
            assertTrue("no trust record exists", h.trust.peers().isEmpty())
            assertEquals("the machine returns to idle", PairingState.IDLE, h.machine.state)
            assertFalse("the window is closed", h.window.isOpen())
            assertNull("the enrollment keys are erased", h.machine.enrollmentKeysForTest())

            val next = h.newMachine(CONN_TWO)
            assertEquals(
                "the consumed code cannot start a second enrollment",
                PREAUTH_REJECT_TYPE,
                frameType(next.onFrame(h.requestFrame())),
            )
        } finally {
            h.close()
        }
    }

    @Test
    fun early_or_mismatched_prepare_erases() = runBlocking {
        // `enrollment_prepare` is phone→client only (§11.1). A wire-valid client→phone record
        // with that type is a direction violation: it must be refused generically, the keys
        // erased, the machine returned to idle, and a previously persisted pending record kept
        // for the recovery path. It is sealed by hand because `RecordCodec.seal` itself refuses
        // the type/direction combination.
        val awaiting = Harness()
        try {
            awaiting.runToCb()
            awaiting.sendClientIdentity()
            assertEquals(PairingState.AWAITING_COMMIT, awaiting.machine.state)
            val keys = checkNotNull(awaiting.machine.enrollmentKeysForTest())
            val prepare = sealClientPrepare(awaiting.clientKeys, awaiting.clientPrefix, 1uL)

            val outgoing = awaiting.machine.onFrame(prepare)

            assertEquals("a client-sent prepare is a direction violation", PREAUTH_REJECT_TYPE, frameType(outgoing))
            assertEquals(PairingState.IDLE, awaiting.machine.state)
            assertTrue("every key field is zero", wiped(keys))
            assertFalse("the window stays consumed", awaiting.window.isOpen())
            assertEquals(
                "the persisted pending record survives the direction violation",
                TrustState.PENDING,
                awaiting.trust.peers().single().state,
            )
        } finally {
            awaiting.close()
        }

        // The same record before the client identity is equally a direction violation and
        // publishes nothing.
        val protected = Harness()
        try {
            protected.runToCb()
            assertEquals(PairingState.ENROLLMENT_PROTECTED, protected.machine.state)
            val keys = checkNotNull(protected.machine.enrollmentKeysForTest())
            val prepare = sealClientPrepare(protected.clientKeys, protected.clientPrefix, 0uL)

            val outgoing = protected.machine.onFrame(prepare)

            assertEquals("an early prepare is refused", PREAUTH_REJECT_TYPE, frameType(outgoing))
            assertEquals(PairingState.IDLE, protected.machine.state)
            assertTrue("every key field is zero", wiped(keys))
            assertTrue("nothing is persisted", protected.trust.peers().isEmpty())
        } finally {
            protected.close()
        }
    }

    @Test
    fun cancel_after_pending_deletes_record() = runBlocking {
        val h = Harness()
        try {
            h.runToCb()
            h.sendClientIdentity()
            assertEquals(TrustState.PENDING, h.trust.peers().single().state)
            val keys = checkNotNull(h.machine.enrollmentKeysForTest())
            // A window opened after the consumed cB must be closed by cancel as well.
            h.window.open()
            assertTrue("the post-cB window is open before cancel", h.window.isOpen())

            val outgoing = h.machine.cancel()

            assertEquals("cancel closes the connection", listOf(Outgoing.Disconnect), outgoing)
            assertTrue("cancel deletes the current enrollment's pending record", h.trust.peers().isEmpty())
            assertEquals("cancel deletes through revocation", listOf(h.clientInstallationHex), h.trust.revoked)
            assertTrue("the enrollment keys are erased", wiped(keys))
            assertTrue("the code is erased", h.codeGone())
            assertFalse("the window is closed", h.window.isOpen())
            assertEquals("no core handle survives", 0, fake.openHandles())
            assertEquals(PairingState.IDLE, h.machine.state)
        } finally {
            h.close()
        }
    }

    @Test
    fun code_one_use_after_cB() = runBlocking {
        val h = Harness()
        try {
            h.runToCb()
            h.sendClientIdentity()
            assertFalse("a verified cB consumes the window", h.window.isOpen())
            assertTrue("the code is gone", h.codeGone())

            val second = h.newMachine(CONN_TWO)
            val refusal = second.onFrame(h.requestFrame())
            assertEquals("a second code entry is refused", PREAUTH_REJECT_TYPE, frameType(refusal))

            // A later enrollment failure cannot reopen the consumed code either.
            val failure = h.machine.onFrame(ByteArray(RecordCodec.HEADER + RecordCodec.TAG))
            assertEquals("a malformed protected record fails closed", PREAUTH_REJECT_TYPE, frameType(failure))
            assertFalse("the failure leaves the window closed", h.window.isOpen())
        } finally {
            h.close()
        }
    }

    @Test
    fun hint_does_not_select_identity() = runBlocking {
        val h = Harness()
        try {
            val own = h.phoneInstallationId()
            h.runToCb()

            val phoneRecord = h.phoneRecord
            val opened = h.openPhoneRecord(phoneRecord, sequence = 0u)
            val phoneBody = EnrollmentRecords.decode(opened, IdentityRole.PHONE)
            assertTrue(
                "the phone record always carries the trust-store identity",
                MessageDigest.isEqual(own, phoneBody.installationId),
            )

            // A client-suggested installation id (the connection hint) is a PEER value: it
            // never becomes the local identity and never replaces the repository identity.
            val hinted = ByteArray(16) { (0x40 + it).toByte() }
            val outgoing = h.sendClientIdentity(installationId = hinted)
            val peer = h.trust.peers().single()
            assertEquals("the peer id is the client value", TrustEncoding.hex(hinted), peer.installationId)
            assertTrue("the local identity is unchanged", MessageDigest.isEqual(own, h.phoneInstallationId()))
            assertTrue(
                "a prepare follows the client record persisted under the hinted peer id",
                sawRecord(outputsOf(outgoing), PREPARE_TYPE),
            )
        } finally {
            h.close()
        }
    }

    @Test
    fun erasure_on_success_failure_cancel() = runBlocking {
        val success = Harness()
        try {
            success.runToCb()
            success.sendClientIdentity()
            val keys = checkNotNull(success.machine.enrollmentKeysForTest())
            success.machine.onDisconnect()
            assertTrue("a completed exchange erases its keys when the connection ends", wiped(keys))
            assertEquals("the pending record survives an ordinary disconnect", 1, success.trust.peers().size)
        } finally {
            success.close()
        }

        val failure = Harness()
        try {
            failure.runToCb()
            val keys = checkNotNull(failure.machine.enrollmentKeysForTest())
            failure.sendClientIdentity(deviceType = DeviceType.PHONE)
            assertTrue("a rejected enrollment erases its keys", wiped(keys))
            assertTrue("nothing was persisted", failure.trust.peers().isEmpty())
        } finally {
            failure.close()
        }

        val cancel = Harness()
        try {
            cancel.runToCb()
            val keys = checkNotNull(cancel.machine.enrollmentKeysForTest())
            cancel.machine.cancel()
            assertTrue("cancel erases its keys", wiped(keys))
        } finally {
            cancel.close()
        }
    }

    @Test
    fun record_type_outside_allow_list_erases() = runBlocking {
        val h = Harness()
        try {
            h.runToCb()
            val keys = checkNotNull(h.machine.enrollmentKeysForTest())
            val pull = RecordCodec.seal(
                h.clientKeys.bToAAesKey,
                h.clientKeys.bToANoncePrefix,
                h.clientPrefix,
                Direction.CLIENT_TO_PHONE,
                RecordType.PULL_REQUEST.wire,
                0uL,
                ByteArray(0),
            )

            val outgoing = h.machine.onFrame(pull)

            assertEquals("an unauthorized class is the generic failure", PREAUTH_REJECT_TYPE, frameType(outgoing))
            assertTrue("the protected session is erased", wiped(keys))
            assertEquals(PairingState.IDLE, h.machine.state)
            assertTrue("no record is written", h.trust.peers().isEmpty())
        } finally {
            h.close()
        }
    }

    // -----------------------------------------------------------------------------------------
    // Harness
    // -----------------------------------------------------------------------------------------

    private inner class Harness(private val failPersist: Boolean = false) {
        var now = 1_000L
        private var nonceCounter = 0
        val window = PairingWindow({ now })
        private val admission = PairingAdmission(PairingLimits(), { now })
        private val phoneKeyPair = P256.generate()
        private val phonePublicKey = TrustEncoding.publicKey(phoneKeyPair.public)
        private val keyStore = PhoneIdentityKeyStore(phonePublicKey)
        private val repository = TrustRepository(
            FlowTrustStorage(),
            keyStore,
            { now },
            { BOOT },
            { now },
            ActivationVerifier { _, _, _ -> true },
        )
        val trust = RecordingTrust(repository)
        lateinit var machine: PhonePairingMachine
        var phoneRecord: ByteArray = ByteArray(0)
            private set

        lateinit var context: ByteArray
            private set
        lateinit var clientPrefix: ByteArray
            private set
        lateinit var clientKeys: EnrollmentKeys
            private set
        lateinit var clientPoint: ByteArray
            private set
        var clientInstallationHex: String = TrustEncoding.hex(CLIENT_INSTALLATION)
            private set
        private lateinit var phonePoint: ByteArray
        private var clientNonce: ByteArray = ByteArray(16) { (it + 1).toByte() }
        private val clientConfirm = ByteArray(32) { 0x0b }
        private var phoneConfirm: ByteArray = ByteArray(0)
        private val clientIdentityKey = P256.generate()

        init {
            runBlocking {
                repository.identity()
                machine = newMachine(CONN_ONE)
            }
        }

        fun phoneInstallationId(): ByteArray = TrustEncoding.bytes(
            runBlocking { repository.identity().installationId },
        )

        fun newMachine(conn: Long): PhonePairingMachine = PhonePairingMachine(
            window,
            admission,
            conn,
            if (failPersist) FailingTrust(trust) else trust,
            { fake },
            { now },
            PairingLimits(),
            { ByteArray(16) { _ -> (++nonceCounter).toByte() } },
            { "Phone" },
            { ByteArray(16) { 0x2a } },
        )

        fun requestFrame(): ByteArray = requestFrame(clientNonce)

        fun codeGone(): Boolean = try {
            window.codeBytes()
            false
        } catch (_: IllegalStateException) {
            true
        }

        fun clientPublicKeyHex(): String = TrustEncoding.publicKey(clientIdentityKey.public)

        fun offer() {
            window.open()
            val outgoing = runBlocking { machine.onFrame(requestFrame()) }
            val offer = offerOf(outgoing)
            phonePoint = offer.point
            context = contextOf(offer.pairingId, offer.phoneNonce, clientNonce, offer.caps, CLIENT_CAPS)
            clientPoint = TestPeers.uncompressed(P256.generate().public as ECPublicKey)
        }

        fun point(): List<Outgoing> = runBlocking { machine.onFrame(pointFrame(clientPoint)) }

        fun confirm(): List<Outgoing> {
            now = 3_000L
            phoneConfirm = fake.ca
            clientKeys = EnrollmentSchedule.derive(fake.ke, context)
            clientPrefix = EnrollmentSchedule.sessionIdPrefix(context)
            return runBlocking { machine.onFrame(confirmFrame(clientConfirm)) }.also { phoneRecord = singleRecord(it) }
        }

        fun runToCb() {
            offer()
            point()
            confirm()
        }

        fun clientIdentityBody(deviceType: DeviceType = DeviceType.DESKTOP): ByteArray {
            val record = EnrollmentRecords.create(
                CLIENT_INSTALLATION,
                "Desktop",
                deviceType,
                TestPeers.uncompressed(clientIdentityKey.public as ECPublicKey),
                ByteArray(16) { 0x33 },
            )
            return EnrollmentRecords.encode(record, if (deviceType == DeviceType.PHONE) IdentityRole.PHONE else IdentityRole.CLIENT)
        }

        fun sendClientIdentity(
            installationId: ByteArray? = null,
            deviceType: DeviceType = DeviceType.DESKTOP,
        ): List<Outgoing> {
            val record = EnrollmentRecords.create(
                installationId ?: CLIENT_INSTALLATION,
                "Desktop",
                deviceType,
                TestPeers.uncompressed(clientIdentityKey.public as ECPublicKey),
                ByteArray(16) { 0x33 },
            )
            val body = EnrollmentRecords.encode(record, if (deviceType == DeviceType.PHONE) IdentityRole.PHONE else IdentityRole.CLIENT)
            if (installationId != null) clientInstallationHex = TrustEncoding.hex(installationId)
            val frame = RecordCodec.seal(
                clientKeys.bToAAesKey,
                clientKeys.bToANoncePrefix,
                clientPrefix,
                Direction.CLIENT_TO_PHONE,
                RecordType.ENROLL_IDENTITY.wire,
                0uL,
                body,
            )
            return runBlocking { machine.onFrame(frame) }
        }

        fun expectedTranscript(clientBody: ByteArray): ByteArray = EnrollmentRecords.transcript(
            context,
            phonePoint,
            clientPoint,
            phoneConfirm,
            clientConfirm,
            EnrollmentRecords.encode(
                EnrollmentRecords.decode(openPhoneBody(phoneRecord), IdentityRole.PHONE),
                IdentityRole.PHONE,
            ),
            clientBody,
        )

        fun openPhoneRecord(record: ByteArray, sequence: ULong): ByteArray = openPhoneBody(record, sequence)

        private fun openPhoneBody(record: ByteArray, sequence: ULong = 0uL): ByteArray = RecordCodec.open(
            clientKeys.aToBAesKey,
            clientKeys.aToBNoncePrefix,
            clientPrefix,
            Direction.PHONE_TO_CLIENT,
            sequence,
            record,
        ).body

        fun seedActive(installationIdHex: String) {
            runBlocking {
                repository.identity()
                repository.addPending(
                    PendingPeer(
                        installationIdHex,
                        TrustEncoding.publicKey(P256.generate().public),
                        "Desktop",
                        1,
                        TrustEncoding.hex(ByteArray(32) { 0x44 }),
                    ),
                )
                repository.confirm(installationIdHex, ActivationStep.COMMIT, byteArrayOf(1))
                repository.confirm(installationIdHex, ActivationStep.ACK, byteArrayOf(1))
            }
        }

        fun close() {
            runBlocking { machine.onDisconnect() }
            fake.reset()
        }
    }

    private class EnrollmentCore : PairingCore {
        private val open = LinkedHashSet<Long>()
        private var next = 0L
        private val calls = mutableListOf<String>()
        val ke = ByteArray(16) { 0x11 }
        val ca = ByteArray(32) { 0x0a }
        private val point = TestPeers.uncompressed(P256.generate().public as ECPublicKey)

        override fun start(role: Role, code: ByteArray, context: ByteArray): Started {
            calls += "start"
            val handle = Handle(++next)
            open += handle.bits
            return Started(handle, point.copyOf())
        }

        override fun exchange(handle: Handle, peerPoint: ByteArray): ByteArray {
            calls += "exchange"
            return ca.copyOf()
        }

        override fun confirm(handle: Handle, peerConfirmation: ByteArray): ByteArray {
            calls += "confirm"
            return ByteArray(0)
        }

        override fun finish(handle: Handle): ByteArray {
            calls += "finish"
            open -= handle.bits
            return ke.copyOf()
        }

        override fun close(handle: Handle) {
            calls += "close"
            open -= handle.bits
        }

        override fun openHandles(): Int = open.size

        fun count(name: String): Int = calls.count { it == name }

        fun reset() {
            calls.clear()
        }
    }

    private class PhoneIdentityKeyStore(private val hex: String) : IdentityKeyStore {
        private var present = false

        override fun publicKey(): String? = if (present) hex else null

        override fun generate(): String {
            present = true
            return hex
        }

        override fun sign(message: ByteArray): ByteArray = ByteArray(0)
    }

    private class RecordingTrust(private val delegate: InstallationTrustRepository) : InstallationTrustRepository {
        var addPendingCalls = 0
            private set
        val revoked = mutableListOf<String>()

        override suspend fun identity(): InstallationIdentity = delegate.identity()
        override suspend fun peers(): List<TrustedPeer> = delegate.peers()
        override suspend fun activePeers(): List<TrustedPeer> = delegate.activePeers()

        override suspend fun addPending(peer: PendingPeer) {
            addPendingCalls += 1
            delegate.addPending(peer)
        }

        override suspend fun confirm(peerId: String, step: ActivationStep, proof: ByteArray) = delegate.confirm(peerId, step, proof)

        override suspend fun revoke(peerId: String) {
            revoked += peerId
            delegate.revoke(peerId)
        }

        override suspend fun markAuthenticated(peerId: String) = delegate.markAuthenticated(peerId)
    }

    private class FailingTrust(private val delegate: InstallationTrustRepository) : InstallationTrustRepository by delegate {
        override suspend fun addPending(peer: PendingPeer): Unit = error("durable storage unavailable")
    }

    private class FlowTrustStorage : TrustStorage {
        private var value: TrustSnapshot? = null

        override suspend fun <T> transaction(block: (TrustSnapshot?) -> Pair<TrustSnapshot, T>): T {
            val (next, result) = block(value)
            value = next
            return result
        }
    }

    private companion object {
        const val U8_MASK = 0xFF
        const val BITS_PER_BYTE = 8
        const val U16_BYTES = 2
        const val HEADER = 3
        const val NONCE_BYTES = 16
        const val PAIRING_ID_BYTES = 16
        const val POINT_BYTES = 65
        const val CONFIRMATION_BYTES = 32
        const val PREPARE_BODY_BYTES = 32
        const val SEQUENCE_OFFSET = 5
        const val LENGTH_OFFSET = 13
        const val SESSION_OFFSET = 15
        const val NONCE_PREFIX_BYTES = 4
        const val GCM_TAG_BITS = 128
        const val REQUEST_FIXED = NONCE_BYTES + 4
        const val VERSION_OFFSET = NONCE_BYTES
        const val CAPS_LEN_OFFSET = NONCE_BYTES + 2
        const val CONTEXT_FIXED = 30 + 6 + PAIRING_ID_BYTES + 2 + NONCE_BYTES + NONCE_BYTES + 4
        const val OFFER_TYPE = 0x02
        const val CA_TYPE = 0x04
        const val PREAUTH_REJECT_TYPE = 0x0F
        const val IDENTITY_TYPE = 0x0100
        const val ERROR_TYPE = 0x0003
        const val PREPARE_TYPE = 0x0104
        const val PHONE_TO_CLIENT = 0x02
        const val CONN_ONE = 1L
        const val CONN_TWO = 2L
        const val CONN_THREE = 3L
        const val BOOT = "boot-a"
        val CONTEXT_PREFIX = "Veles SPAKE2 public context v1".toByteArray(Charsets.US_ASCII)
        val CLIENT_CAPS = byteArrayOf(0x01, 0x03)
        val CLIENT_INSTALLATION = ByteArray(16) { (0x10 + it).toByte() }

        fun frameType(outgoing: List<Outgoing>): Int {
            val frame = outgoing.filterIsInstance<Outgoing.Frame>().firstOrNull() ?: return -1
            return frame.bytes[0].toInt() and U8_MASK
        }

        fun recordType(record: ByteArray): Int = ((record[3].toInt() and U8_MASK) shl BITS_PER_BYTE) or (record[4].toInt() and U8_MASK)

        fun direction(record: ByteArray): Int = record[2].toInt() and U8_MASK

        fun singleRecord(outgoing: List<Outgoing>): ByteArray = outgoing.filterIsInstance<Outgoing.Frame>().single().bytes

        fun outputsOf(outgoing: List<Outgoing>): List<ByteArray> = outgoing.filterIsInstance<Outgoing.Frame>().map { it.bytes }

        fun sawRecord(frames: List<ByteArray>, type: Int): Boolean = frames.any { it.size >= 5 && recordType(it) == type }

        fun wiped(keys: EnrollmentKeys): Boolean = keys.isWiped && keys.fields().all { field -> field.all { it == 0.toByte() } }

        fun expectedPrepareMac(key: ByteArray, et: ByteArray): ByteArray = try {
            Mac.getInstance("HmacSHA256").run {
                init(SecretKeySpec(key, "HmacSHA256"))
                doFinal(et + byteArrayOf(0))
            }
        } catch (_: Exception) {
            fail("HMAC unavailable")
            ByteArray(0)
        }

        /**
         * A wire-valid client→phone `enrollment_prepare`: the §7 header (direction `0x01`,
         * type `0x0104`) with a correct AES-256-GCM tag under the client's B-to-A key. The
         * codec itself refuses this type/direction combination, so the record is sealed here.
         */
        fun sealClientPrepare(keys: EnrollmentKeys, prefix: ByteArray, sequence: ULong): ByteArray {
            val body = ByteArray(PREPARE_BODY_BYTES) { 0x5a }
            val header = ByteArray(RecordCodec.HEADER)
            header[0] = 1
            header[1] = 1
            header[2] = Direction.CLIENT_TO_PHONE.wire.toByte()
            header[3] = (RecordType.ENROLLMENT_PREPARE.wire ushr BITS_PER_BYTE).toByte()
            header[4] = RecordType.ENROLLMENT_PREPARE.wire.toByte()
            ByteBuffer.wrap(header).putLong(SEQUENCE_OFFSET, sequence.toLong())
            header[LENGTH_OFFSET] = (body.size ushr BITS_PER_BYTE).toByte()
            header[LENGTH_OFFSET + 1] = body.size.toByte()
            prefix.copyInto(header, SESSION_OFFSET)
            val nonce = ByteArray(NONCE_PREFIX_BYTES + Long.SIZE_BYTES)
            keys.bToANoncePrefix.copyInto(nonce, 0)
            ByteBuffer.wrap(nonce).putLong(NONCE_PREFIX_BYTES, sequence.toLong())
            val ciphertext = try {
                Cipher.getInstance("AES/GCM/NoPadding").run {
                    init(Cipher.ENCRYPT_MODE, SecretKeySpec(keys.bToAAesKey, "AES"), GCMParameterSpec(GCM_TAG_BITS, nonce))
                    updateAAD(header)
                    doFinal(body)
                }
            } catch (failure: Exception) {
                fail("AES-GCM unavailable: $failure")
                ByteArray(0)
            } finally {
                body.fill(0)
            }
            return header + ciphertext
        }

        fun pointFrame(point: ByteArray): ByteArray = PairingFrames.encode(PairingFrameType.PB, point)

        fun requestFrame(clientNonce: ByteArray): ByteArray {
            val payload = ByteArray(REQUEST_FIXED + CLIENT_CAPS.size)
            clientNonce.copyInto(payload, 0)
            payload[VERSION_OFFSET] = 0
            payload[VERSION_OFFSET + 1] = 1
            payload[CAPS_LEN_OFFSET] = (CLIENT_CAPS.size ushr 8).toByte()
            payload[CAPS_LEN_OFFSET + 1] = CLIENT_CAPS.size.toByte()
            CLIENT_CAPS.copyInto(payload, REQUEST_FIXED)
            return PairingFrames.encode(PairingFrameType.REQUEST, payload)
        }

        fun confirmFrame(confirmation: ByteArray): ByteArray = PairingFrames.encode(PairingFrameType.CB, confirmation)

        fun offerOf(outgoing: List<Outgoing>): Offer {
            val frame = singleRecord(outgoing)
            val payload = frame.copyOfRange(HEADER, frame.size)
            var offset = 0
            val pairingId = payload.copyOfRange(offset, offset + PAIRING_ID_BYTES)
            offset += PAIRING_ID_BYTES
            val nonce = payload.copyOfRange(offset, offset + NONCE_BYTES)
            offset += NONCE_BYTES + U16_BYTES
            val capsLength = u16(payload, offset)
            offset += U16_BYTES
            val caps = payload.copyOfRange(offset, offset + capsLength)
            offset += capsLength
            return Offer(pairingId, nonce, caps, payload.copyOfRange(offset, offset + POINT_BYTES))
        }

        fun contextOf(
            pairingId: ByteArray,
            phoneNonce: ByteArray,
            clientNonce: ByteArray,
            phoneCaps: ByteArray,
            clientCaps: ByteArray,
        ): ByteArray {
            val ctx = ByteArray(CONTEXT_FIXED + phoneCaps.size + clientCaps.size)
            var offset = 0
            CONTEXT_PREFIX.copyInto(ctx, offset)
            offset += CONTEXT_PREFIX.size
            offset = putU16(ctx, offset, 1)
            offset = putU16(ctx, offset, 1)
            offset = putU16(ctx, offset, 1)
            pairingId.copyInto(ctx, offset)
            offset += PAIRING_ID_BYTES
            ctx[offset++] = 0x01
            ctx[offset++] = 0x02
            phoneNonce.copyInto(ctx, offset)
            offset += NONCE_BYTES
            clientNonce.copyInto(ctx, offset)
            offset += NONCE_BYTES
            offset = putU16(ctx, offset, phoneCaps.size)
            phoneCaps.copyInto(ctx, offset)
            offset += phoneCaps.size
            offset = putU16(ctx, offset, clientCaps.size)
            clientCaps.copyInto(ctx, offset)
            return ctx
        }

        fun putU16(target: ByteArray, offset: Int, value: Int): Int {
            target[offset] = (value ushr BITS_PER_BYTE).toByte()
            target[offset + 1] = value.toByte()
            return offset + U16_BYTES
        }

        fun u16(source: ByteArray, offset: Int): Int {
            val high = (source[offset].toInt() and U8_MASK) shl BITS_PER_BYTE
            return high or (source[offset + 1].toInt() and U8_MASK)
        }

        data class Offer(val pairingId: ByteArray, val phoneNonce: ByteArray, val caps: ByteArray, val point: ByteArray)
    }
}
