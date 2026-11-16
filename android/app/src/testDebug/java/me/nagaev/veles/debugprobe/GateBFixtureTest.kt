package me.nagaev.veles.debugprobe

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Debug-only Gate B history fixture (OTP-12 design D3). Five in-memory synthetic records with
 * fixed event ids, the seed's wall-clock receipt epoch and per-record expiry on an injected
 * elapsed-realtime clock. Nothing here sleeps, reads a real clock or touches real OTP data.
 */
class GateBFixtureTest {
    private val installationId = ByteArray(16) { (0xF0 + it).toByte() }
    private val wallNow = 1_700_000_000_123L
    private val clock = ManualElapsedClock()
    private val lines = mutableListOf<String>()
    private var entropyCursor = 0

    /** Deterministic entropy: every record gets a distinct event id and marker. */
    private val random: (Int) -> ByteArray = { size ->
        ByteArray(size) { ((entropyCursor + it) and 0xFF).toByte() }.also { entropyCursor += 7 }
    }

    private fun fixture(): GateBFixture = GateBFixture(
        localInstallationId = installationId,
        elapsed = clock,
        wallMillis = { wallNow },
        random = random,
        log = { lines += it },
    )

    @Test
    fun `seed_rejects_bad_lists`() {
        val fixture = fixture()

        assertFalse("four lifetimes are refused", fixture.seed(List(4) { 600 }))
        assertFalse("six lifetimes are refused", fixture.seed(List(6) { 600 }))
        assertFalse("lifetime 0 is refused", fixture.seed(listOf(0, 600, 600, 600, 600)))
        assertFalse("lifetime 601 is refused", fixture.seed(listOf(601, 600, 600, 600, 600)))
        assertFalse("a refusal never arms", fixture.armed)
        assertTrue("a refusal seeds no record", pull(fixture.pullBody()).isEmpty())
        assertEquals("one refusal line per refused command", 4, lines.count { it.contains("refused") })

        assertTrue(fixture.seed(List(5) { 600 }))
        val ids = pull(fixture.pullBody()).map { hex(it.eventId) }
        val refusals = lines.count { it.contains("refused") }

        assertFalse("a bad list is still refused while armed", fixture.seed(List(4) { 600 }))
        assertTrue("a refusal leaves the fixture armed", fixture.armed)
        assertEquals("a refusal replaces nothing", ids, pull(fixture.pullBody()).map { hex(it.eventId) })
        assertEquals("the refusal is logged", refusals + 1, lines.count { it.contains("refused") })
    }

    @Test
    fun `pull_returns_five_with_distinct_ids_and_600_lifetimes`() {
        val fixture = fixture()
        val lifetimes = listOf(600, 600, 600, 600, 600)

        assertTrue(fixture.seed(lifetimes))
        assertTrue("a valid seed arms the fixture", fixture.armed)

        val events = pull(fixture.pullBody())
        assertEquals(5, events.size)
        assertEquals("every record has its own event id", 5, events.map { hex(it.eventId) }.toSet().size)
        for (event in events) {
            assertArrayEquals(installationId, event.phoneInstallationId)
            assertEquals("receipt epoch is the seed's wall-clock second", 1_700_000_000L, event.receiptEpochSeconds)
            assertEquals(600L, event.remainingLifetime)
            assertEquals("Veles synthetic", event.merchant)
            assertEquals(100L, event.amountMagnitude)
            assertEquals(2, event.amountScale)
            assertEquals("XTS", event.currency)
        }
        assertEquals(
            "a pull before expiry keeps the seed order",
            events.map { hex(it.eventId) },
            pull(fixture.pullBody()).map { hex(it.eventId) },
        )
    }

    @Test
    fun `retained_and_expired_together`() {
        val fixture = fixture()
        assertTrue(fixture.seed(listOf(60, 120, 300, 600, 600)))
        val seeded = pull(fixture.pullBody())
        assertEquals(5, seeded.size)

        clock.advance(150_000)

        val retained = pull(fixture.pullBody())
        assertEquals("the first two records have expired", 3, retained.size)
        assertEquals(
            "the unexpired records keep their original event ids in seed order",
            seeded.drop(2).map { hex(it.eventId) },
            retained.map { hex(it.eventId) },
        )
        assertEquals(
            "remaining_lifetime is the floor of the remaining seconds",
            listOf(150L, 450L, 450L),
            retained.map { it.remainingLifetime },
        )
        assertTrue("the omission is logged with its count", lines.any { it.contains("expired-omitted count=2") })
    }

    @Test
    fun `half_second_remaining_is_omitted`() {
        val fixture = fixture()
        assertTrue(fixture.seed(listOf(1, 600, 600, 600, 600)))
        val seeded = pull(fixture.pullBody())
        assertEquals(5, seeded.size)

        clock.advance(500)

        val retained = pull(fixture.pullBody())
        assertEquals("a record with half a second left is omitted", 4, retained.size)
        assertTrue(
            "the omitted record is the one-second one",
            retained.none { hex(it.eventId) == hex(seeded.first().eventId) },
        )
        assertTrue("remaining_lifetime is never 0", retained.all { it.remainingLifetime >= 1L })
        assertTrue(lines.any { it.contains("expired-omitted count=1") })
    }

    @Test
    fun `all_expired_pull_is_count_zero`() {
        val fixture = fixture()
        assertTrue(fixture.seed(List(5) { 1 }))

        clock.advance(1_000)

        val body = fixture.pullBody()
        assertEquals("an empty result is a count byte only", 1, body.size)
        assertEquals("event_count is 0", 0, body[0].toInt() and 0xFF)
        assertTrue(pull(body).isEmpty())
        assertTrue("every omitted record is counted", lines.any { it.contains("expired-omitted count=5") })
    }

    @Test
    fun `ack_never_deletes_and_unknown_ack_is_false`() {
        val fixture = fixture()
        assertTrue(fixture.seed(List(5) { 600 }))
        val seeded = pull(fixture.pullBody())

        assertTrue("a history id is owned", fixture.acknowledge(seeded.first().eventId))
        assertEquals(
            "an acknowledgement never deletes a record",
            seeded.map { hex(it.eventId) },
            pull(fixture.pullBody()).map { hex(it.eventId) },
        )
        assertFalse("an unknown id is not owned", fixture.acknowledge(ByteArray(16) { 0x7F }))

        val pushed = fixture.nextPush()
        assertTrue("a pushed id is owned", fixture.acknowledge(pushed.eventId))
        assertEquals("a push never joins the history", 5, pull(fixture.pullBody()).size)
        assertTrue(
            "the pushed id is not a history record",
            pull(fixture.pullBody()).none { hex(it.eventId) == hex(pushed.eventId) },
        )

        val pushes = List(20) { fixture.nextPush() }
        assertTrue("the newest pushed id is still owned", fixture.acknowledge(pushes.last().eventId))
        assertFalse("only the last 16 pushed ids are remembered", fixture.acknowledge(pushes.first().eventId))
    }

    @Test
    fun `restart_is_unarmed`() {
        val before = fixture()
        assertTrue(before.seed(List(5) { 600 }))
        assertTrue(before.armed)

        // A fresh process builds a fresh fixture: memory only, no records, unarmed.
        val restarted = fixture()

        assertFalse("a restarted process is unarmed", restarted.armed)
        assertTrue("a restarted process holds no records", pull(restarted.pullBody()).isEmpty())
    }

    @Test
    fun `log_has_no_key_or_full_installation_id`() {
        val fixture = fixture()
        assertTrue(fixture.seed(List(5) { 600 }))
        val seeded = pull(fixture.pullBody())
        assertTrue(fixture.acknowledge(seeded.first().eventId))
        clock.advance(600_000)
        fixture.pullBody()
        fixture.disarm()

        val installationHex = hex(installationId)
        assertTrue("the run logs its outcomes", lines.isNotEmpty())
        for (line in lines) {
            assertFalse("no full installation id in $line", line.contains(installationHex))
            assertFalse("not even the id prefix in $line", line.contains(installationHex.take(8)))
            assertFalse("no key material in $line", line.contains("key="))
        }
        assertTrue("a bounded seed line", lines.any { it.startsWith("elapsed=") && it.contains("seeded") })
        assertTrue("the seed line carries the markers", lines.any { it.contains("markers=${seeded.first().code}") })
        assertTrue("the seed line carries the event ids", lines.any { it.contains("events=${hex(seeded.first().eventId)}") })
        assertTrue(
            "the ack line carries only the event id",
            lines.any { it.endsWith("ack event=${hex(seeded.first().eventId)}") },
        )
        assertTrue("disarm is logged", lines.any { it.contains("disarmed") })
        assertTrue("every line carries the elapsed timestamp", lines.all { it.startsWith("elapsed=") })
    }

    private fun pull(body: ByteArray): List<DecodedEvent> {
        val reader = EventReader(body)
        val count = reader.u8()
        val events = List(count) { reader.event() }
        assertEquals("no trailing bytes", 0, reader.remaining())
        return events
    }

    /** A hand-advanced elapsed-realtime clock; the fixture never reads a wall clock for expiry. */
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
