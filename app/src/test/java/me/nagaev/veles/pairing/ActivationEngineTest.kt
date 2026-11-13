package me.nagaev.veles.pairing

import kotlinx.coroutines.test.runTest
import me.nagaev.veles.session.ApplicationHandler
import me.nagaev.veles.session.LocalRecordState
import me.nagaev.veles.session.PeerLookup
import me.nagaev.veles.session.PeerRecord
import me.nagaev.veles.session.PhoneSessionEngine
import me.nagaev.veles.session.RecordType
import me.nagaev.veles.session.SessionClass
import me.nagaev.veles.session.SessionEffect
import me.nagaev.veles.session.SessionReject
import me.nagaev.veles.session.TestClient
import me.nagaev.veles.session.TestIdentity
import me.nagaev.veles.session.TestPeers
import me.nagaev.veles.session.completeHandshake
import me.nagaev.veles.session.composeApplicationHandlers
import me.nagaev.veles.session.debugRejectAllVerifier
import me.nagaev.veles.session.openPhoneRecord
import me.nagaev.veles.trust.ActivationStep
import me.nagaev.veles.trust.ActivationVerifier
import me.nagaev.veles.trust.IdentityKeyStore
import me.nagaev.veles.trust.InstallationTrustRepository
import me.nagaev.veles.trust.PendingPeer
import me.nagaev.veles.trust.TrustEncoding
import me.nagaev.veles.trust.TrustRepository
import me.nagaev.veles.trust.TrustSnapshot
import me.nagaev.veles.trust.TrustState
import me.nagaev.veles.trust.TrustStorage
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File
import java.security.KeyPairGenerator
import java.security.SecureRandom
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * OTP-10 plan 9d: the phone activation engine over the session-bound proof registry
 * (OTP2-STA-010b steps 0, 5 and 6).
 *
 * Every test drives the real [me.nagaev.veles.session.PhoneSessionEngine] and a real
 * [TrustRepository] whose verifier is the [ProofRegistry] under test; the scripted client uses
 * the production reconnect schedule and MAC input shape `ET || stage_byte`.
 */
class ActivationEngineTest {
    private val et = ByteArray(HASH_BYTES) { 0x42 }
    private val clock = me.nagaev.veles.session.FakeClock()

    @Test
    fun activation_full_flow() = runTest {
        val client = TestClient()
        val harness = harness(TrustState.PENDING, client)
        val authenticated = harness.open(client, peerState = BELOW_ACTIVE)

        val prepare = authenticated.unprompted.single()
        assertEquals(RecordType.ENROLLMENT_PREPARE.wire, prepare.type)
        assertArrayEquals(mac(client.recovery().kRecPhone, STAGE_PREPARE), prepare.body)
        val prepareSend = harness.engine.sendApplication(prepare.type, prepare.body).single() as SessionEffect.Send
        assertArrayEquals(prepare.body, client.openPhoneRecord(prepareSend.bytes, 1uL).body)

        val commit = mac(client.recovery().kRecClient, STAGE_COMMIT)
        val commitEffects = harness.engine.onInbound(
            client.record(RecordType.ACTIVATION_COMMIT.wire, 1uL, commit),
        )
        assertTrue("the commit is delivered", commitEffects.any { it is SessionEffect.Deliver })
        val complete = commitEffects.filterIsInstance<SessionEffect.Send>().single()
        assertArrayEquals(
            mac(client.recovery().kRecPhone, STAGE_COMPLETE),
            client.openPhoneRecord(complete.bytes, 2uL).body,
        )
        assertEquals(TrustState.COMMITTED_WAITING_ACK, harness.peers().single().state)

        val ack = mac(client.recovery().kRecClient, STAGE_ACK)
        val ackEffects = harness.engine.onInbound(
            client.record(RecordType.ACTIVATION_ACK.wire, 2uL, ack),
        )
        assertTrue(ackEffects.none { it is SessionEffect.Terminated })

        assertEquals(TrustState.ACTIVE, harness.peers().single().state)
        // OTP2-STA-010b step 6: the class fixed at the gate never becomes full mid-session.
        assertEquals(SessionClass.ACTIVATION_RECOVERY, harness.engine.sessionClass)
        assertFalse(
            me.nagaev.veles.session.RecordPolicy.authorized(
                RecordType.PULL_REQUEST.wire,
                me.nagaev.veles.session.Direction.PHONE_TO_CLIENT,
                SessionClass.ACTIVATION_RECOVERY,
            ),
        )
    }

    @Test
    fun wrong_mac_changes_nothing() = runTest {
        val commitClient = TestClient()
        val commitHarness = harness(TrustState.PENDING, commitClient)
        commitHarness.open(commitClient, peerState = BELOW_ACTIVE)
        val badCommit = mac(commitClient.recovery().kRecClient, STAGE_COMMIT).also {
            it[it.size - 1] = (it[it.size - 1].toInt() xor 0x01).toByte()
        }

        val effects = commitHarness.engine.onInbound(
            commitClient.record(RecordType.ACTIVATION_COMMIT.wire, 1uL, badCommit),
        )

        assertTrue("a wrong commit ends the session", effects.any { it is SessionEffect.Terminated })
        // OTP2-CRY-019g: a wrong activation MAC never leaves a half-activated record.
        assertTrue("a wrong commit deletes the incomplete record", commitHarness.peers().isEmpty())
        assertFalse(commitHarness.engine.isOpen)

        val ackClient = TestClient()
        val ackHarness = harness(TrustState.PENDING, ackClient)
        ackHarness.open(ackClient, peerState = BELOW_ACTIVE)
        ackHarness.engine.sendApplication(
            RecordType.ENROLLMENT_PREPARE.wire,
            mac(ackClient.recovery().kRecPhone, STAGE_PREPARE),
        )
        ackHarness.engine.onInbound(
            ackClient.record(
                RecordType.ACTIVATION_COMMIT.wire,
                1uL,
                mac(ackClient.recovery().kRecClient, STAGE_COMMIT),
            ),
        )
        assertEquals(TrustState.COMMITTED_WAITING_ACK, ackHarness.peers().single().state)
        val badAck = mac(ackClient.recovery().kRecClient, STAGE_ACK).also {
            it[0] = (it[0].toInt() xor 0x02).toByte()
        }

        val ackEffects = ackHarness.engine.onInbound(
            ackClient.record(RecordType.ACTIVATION_ACK.wire, 2uL, badAck),
        )

        assertTrue("a wrong ack ends the session", ackEffects.any { it is SessionEffect.Terminated })
        assertTrue("a wrong ack deletes the incomplete record", ackHarness.peers().isEmpty())
        assertFalse(ackHarness.engine.isOpen)
    }

    @Test
    fun step0_prepare_unprompted() = runTest {
        val client = TestClient()
        val pendingRecovery = harness(TrustState.PENDING, client)
        val recovery = pendingRecovery.open(client, peerState = BELOW_ACTIVE)
        assertEquals(SessionClass.ACTIVATION_RECOVERY, recovery.sessionClass)
        assertEquals(RecordType.ENROLLMENT_PREPARE.wire, recovery.unprompted.single().type)
        assertArrayEquals(
            mac(client.recovery().kRecPhone, STAGE_PREPARE),
            recovery.unprompted.single().body,
        )

        val activeClient = TestClient()
        val fullPending = harness(TrustState.ACTIVE, activeClient)
        val fullPendingAuth = fullPending.open(activeClient, peerState = BELOW_ACTIVE)
        assertEquals(SessionClass.FULL_PENDING_PEER, fullPendingAuth.sessionClass)
        assertEquals(RecordType.ENROLLMENT_PREPARE.wire, fullPendingAuth.unprompted.single().type)

        // The client reported 0x01: it is beyond commit, so the phone sends no prepare.
        val activeFamilyClient = TestClient()
        val committed = harness(TrustState.COMMITTED_WAITING_ACK, activeFamilyClient)
        val activeFamilyAuth = committed.open(activeFamilyClient, peerState = ACTIVE)
        assertEquals(SessionClass.ACTIVATION_RECOVERY, activeFamilyAuth.sessionClass)
        assertTrue(activeFamilyAuth.unprompted.isEmpty())
    }

    @Test
    fun committed_phone_duplicate_commit_re_sends_complete() = runTest {
        val client = TestClient()
        // The phone is already committed (activation_complete was lost); the client reported
        // 0x02, so step 0 still sends a fresh prepare and the duplicate commit must re-send a
        // fresh activation_complete without moving the record.
        val harness = harness(TrustState.COMMITTED_WAITING_ACK, client)
        val authenticated = harness.open(client, peerState = BELOW_ACTIVE)
        assertEquals(RecordType.ENROLLMENT_PREPARE.wire, authenticated.unprompted.single().type)

        val prepareSend = harness.engine.sendApplication(
            authenticated.unprompted.single().type,
            authenticated.unprompted.single().body,
        ).single() as SessionEffect.Send
        client.openPhoneRecord(prepareSend.bytes, 1uL)
        val commitEffects = harness.engine.onInbound(
            client.record(
                RecordType.ACTIVATION_COMMIT.wire,
                1uL,
                mac(client.recovery().kRecClient, STAGE_COMMIT),
            ),
        )
        val complete = commitEffects.filterIsInstance<SessionEffect.Send>().single()
        assertArrayEquals(
            mac(client.recovery().kRecPhone, STAGE_COMPLETE),
            client.openPhoneRecord(complete.bytes, 2uL).body,
        )
        assertEquals(TrustState.COMMITTED_WAITING_ACK, harness.peers().single().state)
        assertTrue(commitEffects.any { it is SessionEffect.Deliver })
    }

    @Test
    fun recovery_class_never_upgrades() = runTest {
        val client = TestClient()
        val harness = harness(TrustState.PENDING, client)
        harness.open(client, peerState = BELOW_ACTIVE)
        harness.engine.sendApplication(
            RecordType.ENROLLMENT_PREPARE.wire,
            mac(client.recovery().kRecPhone, STAGE_PREPARE),
        )
        harness.engine.onInbound(
            client.record(
                RecordType.ACTIVATION_COMMIT.wire,
                1uL,
                mac(client.recovery().kRecClient, STAGE_COMMIT),
            ),
        )
        harness.engine.onInbound(
            client.record(
                RecordType.ACTIVATION_ACK.wire,
                2uL,
                mac(client.recovery().kRecClient, STAGE_ACK),
            ),
        )
        assertEquals(TrustState.ACTIVE, harness.peers().single().state)

        // The record became active during the session, but the class and its OTP policy do not move.
        assertEquals(SessionClass.ACTIVATION_RECOVERY, harness.engine.sessionClass)
        try {
            harness.engine.sendApplication(RecordType.PULL_REQUEST.wire, ByteArray(0))
            fail("a recovery-class session must not authorize OTP data")
        } catch (_: SessionReject) {
            // Expected: the class stays recovery-only through its own teardown.
        }
    }

    @Suppress("MaxLineLength") // The behavioral assertion names the exact debug verifier.
    @Test
    fun debug_repositories_stay_reject_all() = runTest {
        val proofs = ProofRegistry()
        // First show the proof is valid for the production-style registry verifier.
        val registryRepository = repository(
            ActivationVerifier { peer, step, proof ->
                proofs.consume(peer, step, proof)
            },
        )
        val peer = seed(registryRepository, proofs, TrustState.PENDING, CLIENT_INSTALLATION_ID, TestIdentity.generate().publicKey)
        proofs.register("production", listOf(ActivationProof(peer, ActivationStep.COMMIT, SEED_COMMIT)))
        registryRepository.confirm(peer, ActivationStep.COMMIT, SEED_COMMIT)
        assertEquals(TrustState.COMMITTED_WAITING_ACK, registryRepository.peers().single().state)

        // The exact debug verifier rejects the same proof, and the debug call sites pass that
        // verifier as the createAndroidTrustRepository argument (not merely declare it).
        val debugRepository = repository(debugRejectAllVerifier)
        val debugPeer = seed(debugRepository, ProofRegistry(), TrustState.PENDING, CLIENT_INSTALLATION_ID, TestIdentity.generate().publicKey)
        assertFalse(
            "the debug verifier rejects the production-valid proof",
            debugRejectAllVerifier.verify(debugRepository.peers().single(), ActivationStep.COMMIT, SEED_COMMIT),
        )
        try {
            debugRepository.confirm(debugPeer, ActivationStep.COMMIT, SEED_COMMIT)
            fail("the debug repositories must keep the reject-all verifier")
        } catch (_: Exception) {
            // Expected: the reject-all verifier never accepts a session proof.
        }
        assertEquals(TrustState.PENDING, debugRepository.peers().single().state)

        val files = listOf(
            "src/debug/java/me/nagaev/veles/session/BuildSessionExtensions.kt",
            "src/debug/java/me/nagaev/veles/debugprobe/ProbeReceiver.kt",
        )
        files.forEach { path ->
            val source = File(path).takeIf { it.exists() }
                ?: File("app/$path").takeIf { it.exists() }
                ?: error("missing debug source $path")
            assertTrue(
                "$path must pass debugRejectAllVerifier to the repository",
                source.readText().contains("createAndroidTrustRepository(context, debugRejectAllVerifier)"),
            )
        }
    }

    @Test
    fun replayed_recovery_record_rejected() = runTest {
        val first = TestClient()
        val harness = harness(TrustState.PENDING, first)
        harness.open(first, peerState = BELOW_ACTIVE)
        harness.engine.sendApplication(
            RecordType.ENROLLMENT_PREPARE.wire,
            mac(first.recovery().kRecPhone, STAGE_PREPARE),
        )
        val replay = first.record(
            RecordType.ACTIVATION_COMMIT.wire,
            1uL,
            mac(first.recovery().kRecClient, STAGE_COMMIT),
        )
        harness.engine.onInbound(replay)
        assertEquals(TrustState.COMMITTED_WAITING_ACK, harness.peers().single().state)

        // A new session derives fresh recovery keys: the old session's record cannot open here.
        val second = TestClient(ephemeral = me.nagaev.veles.session.P256.generate(), nonce = randomBytes())
        val secondHarness = harness(TrustState.PENDING, second)
        secondHarness.open(second, peerState = BELOW_ACTIVE)

        val effects = secondHarness.engine.onInbound(replay)

        assertTrue("a foreign-session record is rejected", effects.any { it is SessionEffect.Terminated })
        assertEquals(TrustState.PENDING, secondHarness.peers().single().state)
        assertFalse(secondHarness.engine.isOpen)
    }

    @Test
    fun release_style_null_extension_handler_still_activates() = runTest {
        val client = TestClient()
        val harness = harness(TrustState.PENDING, client)
        val composed = composeApplicationHandlers(harness.activation, null)
        checkNotNull(composed) { "the activation handler keeps a null extension handler alive" }
        val engine = harness.engineWith(composed)
        val authenticated = engine.completeHandshake(client, BELOW_ACTIVE, et)
            .single() as SessionEffect.Authenticated
        assertEquals(SessionClass.ACTIVATION_RECOVERY, authenticated.sessionClass)
        assertEquals(RecordType.ENROLLMENT_PREPARE.wire, authenticated.unprompted.single().type)

        val prepareSend = engine.sendApplication(
            authenticated.unprompted.single().type,
            authenticated.unprompted.single().body,
        ).single() as SessionEffect.Send
        client.openPhoneRecord(prepareSend.bytes, 1uL)
        engine.onInbound(
            client.record(
                RecordType.ACTIVATION_COMMIT.wire,
                1uL,
                mac(client.recovery().kRecClient, STAGE_COMMIT),
            ),
        )
        engine.onInbound(
            client.record(
                RecordType.ACTIVATION_ACK.wire,
                2uL,
                mac(client.recovery().kRecClient, STAGE_ACK),
            ),
        )

        assertEquals(TrustState.ACTIVE, harness.peers().single().state)
    }

    // ---------------------------------------------------------------------------------------
    // Harness
    // ---------------------------------------------------------------------------------------

    private inner class Harness(val repository: TrustRepository, val proofs: ProofRegistry, val activation: ActivationEngine) {
        val engine: PhoneSessionEngine = engineWith(activation)

        fun engineWith(handler: ApplicationHandler): PhoneSessionEngine = PhoneSessionEngine(
            localInstallationId = TestPeers.phoneInstallationId,
            signer = TestIdentity.generate().signer,
            lookup = lookup(repository),
            clock = { clock.now },
            ephemeral = { TestPeers.phoneEphemeral },
            nonce = { TestPeers.phoneNonce },
            handler = handler,
        )

        @Suppress("MaxLineLength") // ktlint keeps a single expression at this width.
        suspend fun open(client: TestClient, peerState: Int): SessionEffect.Authenticated =
            engine.completeHandshake(client, peerState, et).single() as SessionEffect.Authenticated

        suspend fun peers() = repository.peers()
    }

    private suspend fun harness(state: TrustState, client: TestClient): Harness {
        val proofs = ProofRegistry()
        val repository = repository(ActivationVerifier { peer, step, proof -> proofs.consume(peer, step, proof) })
        seed(repository, proofs, state, client.installationId, client.identity.publicKey)
        return Harness(repository, proofs, ActivationEngine(repository, proofs))
    }

    /** Persists the pending peer, promoting it to [state] through the same proof registry. */
    private suspend fun seed(
        repository: TrustRepository,
        proofs: ProofRegistry,
        state: TrustState,
        installationId: ByteArray,
        publicKey: ByteArray,
    ): String {
        val peerId = TrustEncoding.hex(installationId)
        repository.addPending(
            PendingPeer(
                installationId = peerId,
                publicKey = TrustEncoding.hex(publicKey),
                label = "Desktop",
                deviceType = 1,
                enrollmentTranscript = TrustEncoding.hex(et),
            ),
        )
        if (state != TrustState.PENDING) {
            proofs.register("seed", listOf(ActivationProof(peerId, ActivationStep.COMMIT, SEED_COMMIT)))
            repository.confirm(peerId, ActivationStep.COMMIT, SEED_COMMIT)
            if (state == TrustState.ACTIVE) {
                proofs.register("seed", listOf(ActivationProof(peerId, ActivationStep.ACK, SEED_ACK)))
                repository.confirm(peerId, ActivationStep.ACK, SEED_ACK)
            }
        }
        return peerId
    }

    private fun lookup(repository: InstallationTrustRepository): PeerLookup = PeerLookup { id ->
        repository.peers().firstOrNull {
            TrustEncoding.bytes(it.installationId).contentEquals(id)
        }?.let {
            PeerRecord(
                installationId = TrustEncoding.bytes(it.installationId),
                identityPublicKey = TrustEncoding.bytes(it.publicKey),
                state = when (it.state) {
                    TrustState.ACTIVE -> LocalRecordState.ACTIVE
                    TrustState.PENDING -> LocalRecordState.PENDING
                    TrustState.COMMITTED_WAITING_ACK -> LocalRecordState.COMMITTED_WAITING_ACK
                },
                et = TrustEncoding.bytes(it.enrollmentTranscript),
            )
        }
    }

    private fun repository(verifier: ActivationVerifier): TrustRepository {
        val generator = KeyPairGenerator.getInstance("EC").apply { initialize(256) }
        val publicKey = TrustEncoding.publicKey(generator.generateKeyPair().public)
        val keys = object : IdentityKeyStore {
            private var generated = false
            override fun publicKey(): String? = if (generated) publicKey else null
            override fun generate(): String {
                generated = true
                return publicKey
            }
            override fun sign(message: ByteArray): ByteArray = ByteArray(0)
        }
        return TrustRepository(
            MemoryTrustStorage(),
            keys,
            { clock.now },
            { BOOT_TOKEN },
            { clock.now },
            verifier,
        )
    }

    private fun TestClient.recovery() = keys.recovery ?: error("the pending record must derive recovery keys")

    private fun mac(key: ByteArray, stage: Byte): ByteArray = try {
        Mac.getInstance(HMAC).run {
            init(SecretKeySpec(key, HMAC))
            doFinal(et + byteArrayOf(stage))
        }
    } catch (failure: Exception) {
        throw IllegalStateException("test MAC failed", failure)
    }

    private fun randomBytes(): ByteArray = ByteArray(16).also(SecureRandom()::nextBytes)

    private companion object {
        const val HASH_BYTES = 32
        const val BELOW_ACTIVE = 0x02
        const val ACTIVE = 0x01
        const val HMAC = "HmacSHA256"
        const val BOOT_TOKEN = "boot-a"
        val CLIENT_INSTALLATION_ID = ByteArray(16) { (0x30 + it).toByte() }
        val SEED_COMMIT = byteArrayOf(0x01)
        val SEED_ACK = byteArrayOf(0x02)
        const val STAGE_PREPARE: Byte = 0x00
        const val STAGE_COMMIT: Byte = 0x01
        const val STAGE_COMPLETE: Byte = 0x02
        const val STAGE_ACK: Byte = 0x03
    }
}

private class MemoryTrustStorage : TrustStorage {
    private var value: TrustSnapshot? = null

    override suspend fun <T> transaction(block: (TrustSnapshot?) -> Pair<TrustSnapshot, T>): T {
        val (next, result) = block(value)
        value = next
        return result
    }
}
