package me.nagaev.veles.debugprobe

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import me.nagaev.veles.session.FakeClock
import me.nagaev.veles.session.LocalRecordState
import me.nagaev.veles.session.PreAuthCodec
import me.nagaev.veles.session.RecordType
import me.nagaev.veles.session.Role
import me.nagaev.veles.session.SignatureFrame
import me.nagaev.veles.session.TestClient
import me.nagaev.veles.session.TestIdentity
import me.nagaev.veles.session.TestPeers
import me.nagaev.veles.session.openPhoneRecord
import me.nagaev.veles.sharing.ConnectionGeneration
import me.nagaev.veles.sharing.RecordAdmission
import me.nagaev.veles.sharing.SessionHost
import me.nagaev.veles.sharing.SessionTransport
import me.nagaev.veles.trust.ActivationStep
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
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Debug-only synthetic probe (OTP-09 design D5). The handler answers `pull_request` with one
 * §8.1 event, the adb command pushes one event over every open `full` session, and revocation
 * stays impossible while a session is live. Nothing here reads real notifications.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class SyntheticProbeHandlerTest {
    private val installationId = ByteArray(16) { (it + 1).toByte() }
    private val epochMillis = 1_700_000_000_123L
    private val random: (Int) -> ByteArray = { size -> ByteArray(size) }
    private val clock = FakeClock()
    private val transport = RecordingTransport()

    private fun handler(log: (String) -> Unit = {}) = SyntheticProbeHandler(
        localInstallationId = installationId,
        clock = { epochMillis },
        random = random,
        log = log,
    )

    private fun TestScope.host(
        repository: InstallationTrustRepository = RecordingTrust(),
        sessionTransport: SessionTransport = transport,
        factory: (ConnectionGeneration) -> me.nagaev.veles.session.PhoneSessionEngine,
    ): SessionHost = SessionHost(backgroundScope, sessionTransport, factory, repository, { clock.now })

    private fun TestScope.commands(
        sessions: ProbeSessions,
        repository: InstallationTrustRepository = RecordingTrust(),
        log: (String) -> Unit = { transport.log += it },
    ): ProbeCommands = ProbeCommands(
        sessions = sessions,
        repository = repository,
        injector = DebugTrustInjector(
            storage = MemoryStorage(emptySnapshot()),
            elapsedMillis = { 0 },
            wallMillis = { 0 },
        ),
        handler = handler(log),
        log = log,
    )

    private fun TestScope.authenticate(
        host: SessionHost,
        client: TestClient,
        gen: ConnectionGeneration,
        state: LocalRecordState = LocalRecordState.ACTIVE,
    ) {
        host.onRecord(gen, client.helloFrame.copyOf(), admission())
        runCurrent()
        val handshake = transport.sent.filter { it.gen == gen }
        assertEquals("phone hello and signature", 2, handshake.size)
        val phoneHelloPayload = PreAuthCodec.decode(handshake[0].bytes).second
        val phoneSignature = SignatureFrame.decode(
            PreAuthCodec.decode(handshake[1].bytes).second,
            Role.PHONE,
        ).signature
        client.acceptPhoneHello(phoneHelloPayload, phoneSignature)
        host.onRecord(gen, client.signatureFrame(), admission())
        runCurrent()
        val token = requireNotNull(transport.sent.last().token) { "the phone reconnect_confirm must be tokened" }
        host.onRecord(gen, client.confirmRecord(), admission())
        runCurrent()
        host.onSendComplete(gen, token, true)
        runCurrent()
    }

    private fun TestScope.connectFull(
        host: SessionHost,
        address: String,
        serial: Long,
        client: TestClient = TestClient(),
    ): ConnectionGeneration {
        val gen = ConnectionGeneration(address, serial)
        host.onConnected(gen)
        runCurrent()
        authenticate(host, client, gen)
        return gen
    }

    @Test
    fun pullReturnsOneSection81Event() = runTest {
        val handler = handler()
        handler.onSessionEstablished(ByteArray(8) { (0x40 + it).toByte() })
        var type = -1
        var body = ByteArray(0)
        handler.handle(RecordType.PULL_REQUEST.wire, ByteArray(0)) { replyType, replyBody ->
            type = replyType
            body = replyBody
        }

        assertEquals(RecordType.PULL_EVENTS.wire, type)
        val reader = EventReader(body)
        assertEquals("exactly one synthetic event", 1, reader.u8())
        val event = reader.event()
        assertEquals("no trailing bytes", 0, reader.remaining())
        assertArrayEquals(installationId, event.phoneInstallationId)
        assertArrayEquals(ByteArray(16), event.eventId)
        assertEquals(1_700_000_000L, event.receiptEpochSeconds)
        assertEquals(600L, event.remainingLifetime)
        assertEquals("VAAAAA", event.code)
        assertEquals("Veles synthetic", event.merchant)
        assertEquals(100L, event.amountMagnitude)
        assertEquals(2, event.amountScale)
        assertEquals("XTS", event.currency)
    }

    @Test
    fun pullLogsMarkerEventAndSessionPrefix() = runTest {
        val lines = mutableListOf<String>()
        val handler = handler { lines += it }
        handler.onSessionEstablished(ByteArray(8) { (0x40 + it).toByte() })
        handler.handle(RecordType.PULL_REQUEST.wire, ByteArray(0)) { _, _ -> }

        assertEquals(1, lines.size)
        assertEquals(
            "marker=VAAAAA event=${"00".repeat(16)} sid=4041424344454647",
            lines.single(),
        )
    }

    @Test
    fun pushWithoutOpenSessionSendsNothing() = runTest {
        val host = host(RecordingTrust()) { engine(TestClient()) }
        val commands = commands(SessionProbeSessions(host))

        commands.push()

        assertTrue("nothing is sent without a full session", transport.sent.isEmpty())
        assertTrue("nothing is logged without a session", transport.log.isEmpty())
    }

    @Test
    fun pushBeforeAnyPullIsSent() = runTest {
        val client = TestClient()
        val host = host(RecordingTrust()) { engine(client) }
        val gen = connectFull(host, "aa", 1, client)
        val commands = commands(SessionProbeSessions(host))
        val before = transport.sent.size

        commands.push()

        val pushed = transport.sent.drop(before)
        assertEquals(1, pushed.size)
        assertEquals(gen, pushed.single().gen)
        val opened = client.openPhoneRecord(pushed.single().bytes, 1uL)
        assertEquals(RecordType.PUSH_EVENT.wire, opened.type)
        val event = EventReader(opened.body).event()
        assertEquals(600L, event.remainingLifetime)
        assertEquals("XTS", event.currency)
        assertEquals("VAAAAA", event.code)
        assertEquals(1, transport.log.size)
    }

    @Test
    fun pushSkipsActivationRecoverySessions() {
        runTest {
            val fullClient = TestClient()
            val recoveryClient = TestClient()
            val host = host(RecordingTrust()) { gen ->
                when (gen.address) {
                    "aa" -> engine(fullClient)
                    else -> engine(recoveryClient, LocalRecordState.PENDING)
                }
            }
            val full = connectFull(host, "aa", 1, fullClient)
            val recovery = connectFull(host, "bb", 2, recoveryClient)
            assertEquals(
                "the recovery session is open but not full",
                listOf(full),
                host.openFullSessions(),
            )
            val commands = commands(SessionProbeSessions(host))
            val before = transport.sent.size

            commands.push()

            val pushed = transport.sent.drop(before)
            assertEquals(listOf(full), pushed.map { it.gen })
            assertTrue("a recovery session never carries OTP data", pushed.none { it.gen == recovery })
        }
    }

    @Test
    fun pushReachesEveryOpenFullSession() = runTest {
        val first = TestClient()
        val second = TestClient()
        val host = host(RecordingTrust()) { gen ->
            engine(if (gen.address == "aa") first else second)
        }
        val a = connectFull(host, "aa", 1, first)
        val b = connectFull(host, "bb", 2, second)
        val commands = commands(SessionProbeSessions(host))
        val before = transport.sent.size

        commands.push()

        val pushed = transport.sent.drop(before)
        assertEquals(listOf(a, b), pushed.map { it.gen })
        val bodies = pushed.map { it.bytes.toList() }
        assertEquals("one synthetic event per generation", 1, bodies.distinct().size)
    }

    @Test
    fun revokeRefusedWhileSessionOpen() = runTest {
        val repository = RecordingTrust()
        val client = TestClient()
        val host = host(repository) { engine(client) }
        val gen = connectFull(host, "aa", 1, client)
        val refused = commands(SessionProbeSessions(host), repository)
        val peerId = TrustEncoding.hex(client.installationId)

        refused.revokePeer(peerId)

        assertTrue("an open session blocks the debug revocation", repository.revoked.isEmpty())
        assertTrue("the refusal is logged", refusedLog().contains("refused"))

        // A different, never connected peer is revocable.
        val absent = TrustEncoding.hex(ByteArray(16) { 0x7f })
        refused.revokePeer(absent)
        assertEquals(listOf(absent), repository.revoked)

        host.onDisconnected(gen)
        runCurrent()
    }

    @Test
    fun realNotificationNotUsed() {
        val names = buildList {
            SyntheticProbeHandler::class.java.declaredConstructors.forEach {
                addAll(it.parameterTypes.map(Class<*>::getName))
            }
            SyntheticProbeHandler::class.java.declaredFields.forEach { add(it.type.name) }
        }
        assertTrue(names.isNotEmpty())
        assertFalse(
            "the probe never depends on the notification listener or its messages: $names",
            names.any {
                it.contains("NotificationListener") || it.contains("OtpMessage") ||
                    it.contains("StatusBarNotification")
            },
        )
    }

    @Test
    fun injectWritesAnActivePeerThatValidates() = runTest {
        val local = TestIdentity.generate()
        val localIdentity = InstallationIdentity(
            TrustEncoding.hex(ByteArray(16) { (it + 0x10).toByte() }),
            TrustEncoding.hex(local.publicKey),
        )
        val storage = MemoryStorage(
            TrustSnapshot(
                identity = localIdentity,
                bootMarker = "bb".repeat(32),
                continuityToken = "cc".repeat(32),
            ),
        )
        val peer = TestClient()
        val peerId = TrustEncoding.hex(peer.installationId)
        val peerPublicKey = TrustEncoding.hex(peer.identity.publicKey)

        DebugTrustInjector(
            storage = storage,
            elapsedMillis = { 42L },
            wallMillis = { 7_000L },
        ).injectActive(peerId, peerPublicKey)

        // A real repository re-validates every stored peer on every transaction: reading the
        // record through it proves the injected record passes validatePeer.
        val repository = TrustRepository(
            storage = storage,
            keys = FixedKeys(localIdentity.publicKey),
            elapsedMillis = { 42L },
            bootToken = { "bb".repeat(32) },
            wallMillis = { 7_000L },
            activationVerifier = { _, _, _ -> false },
        )
        val stored = repository.peers().single()
        assertEquals(peerId, stored.installationId)
        assertEquals(peerPublicKey, stored.publicKey)
        assertEquals(TrustState.ACTIVE, stored.state)
        assertNotEquals(localIdentity.installationId, stored.installationId)
    }

    private fun engine(
        client: TestClient,
        state: LocalRecordState = LocalRecordState.ACTIVE,
    ) = TestPeers.engine(
        TestIdentity.generate().signer,
        TestPeers.lookup(TestPeers.record(client, state)),
        clock,
    )

    private fun admission(): RecordAdmission = RecordAdmission { }

    private fun refusedLog(): String = transport.log.joinToString("\n")

    private fun emptySnapshot(): TrustSnapshot = TrustSnapshot(
        identity = InstallationIdentity("11".repeat(16), "04" + "22".repeat(64)),
        bootMarker = "bb".repeat(32),
        continuityToken = "cc".repeat(32),
    )

    private class MemoryStorage(initial: TrustSnapshot) : TrustStorage {
        private var snapshot: TrustSnapshot? = initial
        override suspend fun <T> transaction(block: (TrustSnapshot?) -> Pair<TrustSnapshot, T>): T {
            val (next, result) = block(snapshot)
            snapshot = next
            return result
        }
    }

    private class FixedKeys(private val key: String) : IdentityKeyStore {
        override fun publicKey(): String = key
        override fun generate(): String = error("unused")
        override fun sign(message: ByteArray): ByteArray = error("unused")
    }

    private class RecordingTrust : InstallationTrustRepository {
        val revoked = mutableListOf<String>()
        val marked = mutableListOf<String>()
        override suspend fun identity(): InstallationIdentity = error("unused")
        override suspend fun peers(): List<TrustedPeer> = emptyList()
        override suspend fun activePeers(): List<TrustedPeer> = emptyList()
        override suspend fun addPending(peer: PendingPeer) = Unit
        override suspend fun confirm(peerId: String, step: ActivationStep, proof: ByteArray) = Unit
        override suspend fun revoke(peerId: String) {
            revoked += peerId
        }
        override suspend fun markAuthenticated(peerId: String) {
            marked += peerId
        }
    }

    private class RecordingTransport : SessionTransport {
        data class Sent(val gen: ConnectionGeneration, val bytes: ByteArray, val token: Long?)
        val sent = mutableListOf<Sent>()
        val log = mutableListOf<String>()

        override fun send(gen: ConnectionGeneration, bytes: ByteArray, token: Long?): Boolean {
            sent += Sent(gen, bytes.copyOf(), token)
            return true
        }

        override fun close(gen: ConnectionGeneration) = Unit
    }

    private class EventReader(private val bytes: ByteArray) {
        private var offset = 0

        fun remaining(): Int = bytes.size - offset

        fun u8(): Int {
            require(remaining() >= 1) { "truncated u8" }
            return bytes[offset++].toInt() and 0xFF
        }

        fun u16(): Int = (u8() shl 8) or u8()

        fun u32(): Long {
            var value = 0L
            repeat(4) { value = (value shl 8) or u8().toLong() }
            return value
        }

        fun u64(): Long {
            var value = 0L
            repeat(8) { value = (value shl 8) or u8().toLong() }
            return value
        }

        fun take(size: Int): ByteArray {
            require(remaining() >= size) { "truncated field" }
            return bytes.copyOfRange(offset, offset + size).also { offset += size }
        }

        fun event(): DecodedEvent {
            val phone = take(16)
            val eventId = take(16)
            val epoch = u64()
            val lifetime = u32()
            val code = String(take(u16()), Charsets.UTF_8)
            val merchant = String(take(u16()), Charsets.UTF_8)
            val amount = u64()
            val scale = u8()
            val currency = String(take(3), Charsets.US_ASCII)
            return DecodedEvent(phone, eventId, epoch, lifetime, code, merchant, amount, scale, currency)
        }
    }

    private data class DecodedEvent(
        val phoneInstallationId: ByteArray,
        val eventId: ByteArray,
        val receiptEpochSeconds: Long,
        val remainingLifetime: Long,
        val code: String,
        val merchant: String,
        val amountMagnitude: Long,
        val amountScale: Int,
        val currency: String,
    )
}
