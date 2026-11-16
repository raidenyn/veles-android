package me.nagaev.veles.debugprobe

import kotlinx.coroutines.test.runTest
import me.nagaev.veles.session.ApplicationHandler
import me.nagaev.veles.session.RecordType
import me.nagaev.veles.session.SessionClass
import me.nagaev.veles.session.SessionContext
import me.nagaev.veles.session.SessionReject
import me.nagaev.veles.session.buildDebugHandler
import me.nagaev.veles.session.composeApplicationHandlers
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Debug-only Gate B pull router (OTP-12 design D5 / spec "One responder per pull"). While the
 * fixture is armed it answers `pull_request` and the probe stays silent; the mode is read once
 * per pull so a change mid-pull can never answer twice or leave a pull unanswered. `event_ack`
 * goes to the component that owns the id. Nothing here sleeps or reads a wall clock.
 */
class GateBPullRouterTest {
    private val installationId = ByteArray(16) { (0xF0 + it).toByte() }
    private val wallNow = 1_700_000_000_123L
    private val clock = ManualElapsedClock()
    private val lines = mutableListOf<String>()
    private var entropyCursor = 0

    /** Deterministic entropy: every synthetic event gets a distinct id and marker. */
    private val random: (Int) -> ByteArray = { size ->
        ByteArray(size) { ((entropyCursor + it) and 0xFF).toByte() }.also { entropyCursor += 7 }
    }

    private val zeroRandom: (Int) -> ByteArray = { size -> ByteArray(size) }

    private fun fixture(): GateBFixture = GateBFixture(
        localInstallationId = installationId,
        elapsed = clock,
        wallMillis = { wallNow },
        random = random,
        log = { lines += it },
    )

    private fun probe(random: (Int) -> ByteArray = this.random): SyntheticProbeHandler = SyntheticProbeHandler(
        localInstallationId = installationId,
        clock = { wallNow },
        random = random,
        log = { lines += it },
    )

    private fun router(
        fixture: GateBFixture,
        probe: ApplicationHandler = probe(),
        mode: (() -> Boolean)? = null,
    ): GateBPullRouter = if (mode == null) {
        GateBPullRouter(probe = probe, fixture = fixture, log = { lines += it })
    } else {
        GateBPullRouter(probe = probe, fixture = fixture, mode = mode, log = { lines += it })
    }

    private fun context(prefix: ByteArray): SessionContext = SessionContext(
        sessionIdPrefix = prefix,
        sessionClass = SessionClass.FULL,
        peerId = hex(installationId),
        et = null,
        // The engine's active-peer byte; only the class and prefix matter to the router.
        peerState = 0x01,
        recovery = null,
    )

    @Test
    fun two_sessions_each_get_exactly_one_reply_while_armed() = runTest {
        val fixture = fixture()
        assertTrue(fixture.seed(List(5) { 600 }))
        val composed = checkNotNull(
            composeApplicationHandlers(router(fixture), SiblingHandler()),
        )
        val first = composed.forSession(context(ByteArray(8) { (0x40 + it).toByte() }))
        val second = composed.forSession(context(ByteArray(8) { (0x50 + it).toByte() }))

        val firstReplies = mutableListOf<Pair<Int, ByteArray>>()
        val secondReplies = mutableListOf<Pair<Int, ByteArray>>()
        first.handle(RecordType.PULL_REQUEST.wire, ByteArray(0)) { type, body -> firstReplies += type to body }
        second.handle(RecordType.PULL_REQUEST.wire, ByteArray(0)) { type, body -> secondReplies += type to body }

        assertEquals("one reply per session", 1, firstReplies.size)
        assertEquals("one reply per session", 1, secondReplies.size)
        val firstEvents = events(firstReplies.single().second)
        val secondEvents = events(secondReplies.single().second)
        assertEquals(RecordType.PULL_EVENTS.wire, firstReplies.single().first)
        assertEquals("the armed fixture answers with its five records", 5, firstEvents.size)
        assertEquals("the armed fixture answers with its five records", 5, secondEvents.size)
        assertEquals(
            "both sessions see the fixture's fixed event ids",
            firstEvents.map { hex(it.eventId) },
            secondEvents.map { hex(it.eventId) },
        )
        assertEquals("the fixture answered both pulls", 2, lines.count { it == "pull answered=fixture" })
        assertTrue("the probe never answered", lines.none { it.startsWith("marker=") })
    }

    @Test
    fun mode_flip_between_lookups_answers_once() = runTest {
        val fixture = fixture()
        assertTrue(fixture.seed(List(5) { 600 }))
        var armed = true
        var reads = 0
        val router = router(fixture, probe(), mode = {
            reads++
            armed
        })

        assertTrue("the router handles the pull in either mode", router.handles(RecordType.PULL_REQUEST.wire))
        assertEquals("the handles check does not select a component", 0, reads)

        // The engine's handles check has passed; the seam flips the mode before handle runs.
        armed = false
        var replies = 0
        var body = ByteArray(0)
        router.handle(RecordType.PULL_REQUEST.wire, ByteArray(0)) { _, replyBody ->
            replies += 1
            body = replyBody
        }

        assertEquals("the mode is read exactly once per pull", 1, reads)
        assertEquals("the pull is answered exactly once", 1, replies)
        assertEquals("the single read selected the probe", 1, events(body).size)
        assertTrue(lines.contains("pull answered=probe"))
        assertFalse("the fixture did not answer", lines.contains("pull answered=fixture"))
    }

    @Test
    fun unarmed_behaves_like_probe() = runTest {
        val fixture = fixture()
        assertFalse("a fresh fixture is unarmed", fixture.armed)
        val direct = probe(zeroRandom)
        var directBody = ByteArray(0)
        direct.handle(RecordType.PULL_REQUEST.wire, ByteArray(0)) { _, replyBody -> directBody = replyBody }

        var body = ByteArray(0)
        router(fixture, probe(zeroRandom)).handle(RecordType.PULL_REQUEST.wire, ByteArray(0)) { _, replyBody ->
            body = replyBody
        }

        assertArrayEquals("an unarmed pull is exactly the probe's answer", directBody, body)
        val event = events(body).single()
        assertArrayEquals(installationId, event.phoneInstallationId)
        assertArrayEquals(ByteArray(16), event.eventId)
        assertEquals(600L, event.remainingLifetime)
        assertEquals("VAAAAA", event.code)
        assertEquals("the fixture never answered", 0, lines.count { it == "pull answered=fixture" })
    }

    @Test
    fun ack_routing_for_history_pushed_and_unknown_ids() = runTest {
        val fixture = fixture()
        assertTrue(fixture.seed(List(5) { 600 }))
        val historyId = events(fixture.pullBody()).first().eventId
        val pushedId = fixture.nextPush().eventId
        val router = router(fixture)

        router.handle(RecordType.EVENT_ACK.wire, installationId + historyId) { _, _ -> }
        router.handle(RecordType.EVENT_ACK.wire, installationId + pushedId) { _, _ -> }

        assertTrue(
            "a history id is acknowledged by the fixture",
            lines.any { it.endsWith("ack event=${hex(historyId)}") },
        )
        assertTrue(
            "a pushed id is acknowledged by the fixture",
            lines.any { it.endsWith("ack event=${hex(pushedId)}") },
        )

        val unknown = ByteArray(16) { 0x7F }
        router.handle(RecordType.EVENT_ACK.wire, installationId + unknown) { _, _ -> }
        assertTrue(
            "an unknown id is the OTP2-REC-032a no-op and is never logged by the fixture",
            lines.none { it.contains("ack event=${hex(unknown)}") },
        )

        val wrongInstallation = ByteArray(16) { 0x01 } + historyId
        val failure = runCatching {
            router.handle(RecordType.EVENT_ACK.wire, wrongInstallation) { _, _ -> }
        }.exceptionOrNull()
        assertTrue("a wrong installation id still fails closed as the probe does", failure is SessionReject)
    }

    @Test
    fun disarm_returns_pulls_to_probe() = runTest {
        val fixture = fixture()
        assertTrue(fixture.seed(List(5) { 600 }))
        val router = router(fixture)

        var armedBody = ByteArray(0)
        router.handle(RecordType.PULL_REQUEST.wire, ByteArray(0)) { _, body -> armedBody = body }
        assertEquals("the armed fixture answers five records", 5, events(armedBody).size)

        fixture.disarm()
        assertTrue("the router still handles the pull", router.handles(RecordType.PULL_REQUEST.wire))
        var disarmedBody = ByteArray(0)
        router.handle(RecordType.PULL_REQUEST.wire, ByteArray(0)) { _, body -> disarmedBody = body }
        assertEquals("after disarm the probe answers its single event", 1, events(disarmedBody).size)
        assertTrue("the probe is logged as the answerer", lines.contains("pull answered=probe"))
    }

    @Test
    fun router_logs_which_component_answered() = runTest {
        val fixture = fixture()
        assertTrue(fixture.seed(List(5) { 600 }))
        val router = router(fixture)

        router.handle(RecordType.PULL_REQUEST.wire, ByteArray(0)) { _, _ -> }
        fixture.disarm()
        router.handle(RecordType.PULL_REQUEST.wire, ByteArray(0)) { _, _ -> }

        val answers = lines.filter { it.startsWith("pull answered=") }
        assertEquals("one answer line per pull", listOf("pull answered=fixture", "pull answered=probe"), answers)
    }

    @Test
    fun router_absent_without_identity() {
        // The pure composition branch: no Android logger, no Hilt and no Robolectric needed.
        assertNull("no identity means no router", buildDebugHandler(identity = null, log = {}))
    }

    /** A second composed handler that owns none of the routed records. */
    private class SiblingHandler : ApplicationHandler {
        override fun handles(type: Int): Boolean = false

        override suspend fun handle(type: Int, body: ByteArray, reply: suspend (Int, ByteArray) -> Unit): Unit = throw SessionReject()
    }

    private fun events(body: ByteArray): List<DecodedEvent> {
        val reader = EventReader(body)
        val count = reader.u8()
        val events = List(count) { reader.event() }
        assertEquals("no trailing bytes", 0, reader.remaining())
        return events
    }

    /** A hand-advanced elapsed-realtime clock; nothing here sleeps or reads a real clock. */
    private class ManualElapsedClock(start: Long = 0L) : ElapsedClock {
        private var millis = start

        override fun elapsedMillis(): Long = millis

        fun advance(deltaMillis: Long) {
            millis += deltaMillis
        }
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
