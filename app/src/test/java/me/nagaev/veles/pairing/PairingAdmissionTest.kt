package me.nagaev.veles.pairing

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Test
import java.util.concurrent.CountDownLatch

/**
 * Phone pairing admission budgets (OTP2-CRY-008, sec-spake-attempts): the
 * non-refundable per-`pairing_id` attempt budget, the rolling five-minute budget,
 * the single in-flight slot, the fresh-`pA` budgets and the request/malformed
 * connection budgets.
 */
class PairingAdmissionTest {
    private var now = 1_000L
    private val limits = PairingLimits()
    private val admission = PairingAdmission(limits) { now }
    private val pairingId = ByteArray(16) { (it + 1).toByte() }

    @Test
    fun rolling_limit_across_windows() {
        val next = ByteArray(16) { 0x40 }
        repeat(3) { assertEquals(Decision.Allow, admission.chargeAttempt(pairingId)) }
        repeat(2) { assertEquals(Decision.Allow, admission.chargeAttempt(next)) }
        assertEquals(
            "the sixth attempt in the rolling five minutes is refused even on a fresh pairing_id",
            Decision.Reject,
            admission.chargeAttempt(ByteArray(16) { 0x50 }),
        )

        now += limits.rollingAttemptsIntervalMillis
        assertEquals("the rolling window slides", Decision.Allow, admission.chargeAttempt(next))
    }

    @Test
    fun pa_only_connection_consumes_no_attempt() {
        val single = PairingLimits(attemptsPerPairingId = 1)
        val tight = PairingAdmission(single) { now }

        // A connection that elicits a fresh pA and disconnects without a valid pB.
        assertEquals(Decision.Allow, tight.requestAllowed(CONN_ONE))
        assertEquals(Decision.Allow, tight.paAllowed(CONN_ONE))
        assertEquals("no pB: no charge", 0, tight.attemptsChargedForTest(pairingId))

        assertEquals(Decision.Allow, tight.chargeAttempt(pairingId))
        assertEquals(Decision.Reject, tight.chargeAttempt(pairingId))
    }

    @Test
    fun charge_atomic_versus_cancel_and_deadline() {
        val single = PairingLimits(attemptsPerPairingId = 1)
        val tight = PairingAdmission(single) { now }

        assertEquals(Decision.Allow, tight.chargeAttempt(pairingId))
        // The window is cancelled and the deadline passes; nothing refunds the charge.
        now += single.pairingWindowSeconds * 1_000L
        assertEquals(Decision.Reject, tight.chargeAttempt(pairingId))
        assertEquals(Decision.Reject, tight.chargeAttempt(pairingId.copyOf()))
        assertEquals("the attempt stays consumed", 1, tight.attemptsChargedForTest(pairingId))
    }

    @Test
    fun two_concurrent_charges_one_attempt_left() {
        val single = PairingLimits(attemptsPerPairingId = 1)
        val tight = PairingAdmission(single) { now }
        val start = CountDownLatch(1)
        val results = mutableListOf<Decision>()
        val workers = 2
        val ready = CountDownLatch(workers)
        val threads = (1..workers).map {
            Thread {
                ready.countDown()
                start.await()
                val decision = tight.chargeAttempt(pairingId)
                synchronized(results) { results += decision }
            }.also(Thread::start)
        }
        ready.await()
        start.countDown()
        threads.forEach(Thread::join)

        assertEquals("exactly one of two concurrent charges wins", 1, results.count { it == Decision.Allow })
        assertEquals(1, results.count { it == Decision.Reject })
        assertEquals("both workers reported", 2, results.size)
    }

    @Test
    fun cancel_then_new_window_fresh_budget_rolling_still_applies() {
        val two = PairingLimits(attemptsPerPairingId = 2, rollingAttemptsMax = 2)
        val tight = PairingAdmission(two) { now }
        repeat(2) { assertEquals(Decision.Allow, tight.chargeAttempt(pairingId)) }

        // A new window draws a fresh pairing_id: its per-id budget is fresh...
        val fresh = ByteArray(16) { 0x60 }
        assertEquals(
            "...but the rolling budget still covers the earlier window",
            Decision.Reject,
            tight.chargeAttempt(fresh),
        )

        now += two.rollingAttemptsIntervalMillis
        assertEquals(Decision.Allow, tight.chargeAttempt(fresh))
        assertEquals(Decision.Allow, tight.chargeAttempt(fresh))
        assertEquals(Decision.Reject, tight.chargeAttempt(fresh))
    }

    @Test
    fun sixth_pa_per_minute_rejected() {
        repeat(limits.freshPaRollingMax) { index ->
            assertEquals("request ${index + 1}", Decision.Allow, admission.requestAllowed(index.toLong()))
            assertEquals("pA ${index + 1}", Decision.Allow, admission.paAllowed(index.toLong()))
            now += 1
        }
        assertEquals(Decision.Allow, admission.requestAllowed(FIFTH_INDEX + 1L))
        assertEquals(
            "the sixth fresh pA in a rolling minute is refused before scalar generation",
            Decision.Reject,
            admission.paAllowed(FIFTH_INDEX + 1L),
        )

        now += limits.freshPaRollingIntervalMillis
        assertEquals(Decision.Allow, admission.paAllowed(FIFTH_INDEX + 2L))
    }

    @Test
    fun twenty_first_malformed_connection_blocks_requests() {
        repeat(limits.malformedConnectionsRollingMax - 1) { admission.malformed(it.toLong()) }
        assertEquals(
            "19 malformed connections leave the request budget usable",
            Decision.Allow,
            admission.requestAllowed(MALFORMED_INDEX),
        )

        admission.malformed(MALFORMED_INDEX + 1L)
        assertEquals(
            "the twentieth malformed connection blocks further pairing requests",
            Decision.Reject,
            admission.requestAllowed(MALFORMED_INDEX + 2L),
        )

        now += limits.malformedRollingIntervalMillis
        assertEquals("the malformed budget slides", Decision.Allow, admission.requestAllowed(MALFORMED_INDEX + 3L))
    }

    @Test
    fun eleventh_request_checked_before_pa_limit() {
        val ten = PairingLimits(freshPaRollingMax = 50)
        val tight = PairingAdmission(ten) { now }
        repeat(ten.requestRollingMax) {
            assertEquals(Decision.Allow, tight.requestAllowed(CONN_ONE))
            // The same connection is refused a second pA, but every request still counts.
            tight.paAllowed(CONN_ONE)
        }
        assertEquals("the same connection cannot get a second pA", Decision.Reject, tight.paAllowed(CONN_ONE))

        // A fresh connection still has pA budget, so only the request ceiling can refuse it.
        assertEquals("pA budget still has room", Decision.Allow, tight.paAllowed(CONN_TWO))
        assertEquals(
            "the eleventh request is refused regardless of the pA budget",
            Decision.Reject,
            tight.requestAllowed(CONN_TWO),
        )
    }

    @Test
    fun window_close_drops_the_per_window_counters() {
        val ids = (1..limits.rollingAttemptsMax).map { index -> ByteArray(16) { index.toByte() } }
        ids.forEach { assertEquals(Decision.Allow, admission.chargeAttempt(it)) }
        assertEquals(Decision.Allow, admission.paAllowed(CONN_ONE))
        assertEquals("the connection already holds its pA", Decision.Reject, admission.paAllowed(CONN_ONE))
        assertEquals(1, admission.attemptsChargedForTest(ids.first()))

        admission.onWindowClosed()

        assertEquals("the closed window's per-id attempt counters are dropped", 0, admission.attemptsChargedForTest(ids.first()))
        assertEquals("the per-connection pA counters are dropped with the window", Decision.Allow, admission.paAllowed(CONN_ONE))
        assertEquals(
            "the rolling attempt budget survives the window close",
            Decision.Reject,
            admission.chargeAttempt(ByteArray(16) { 0x7F }),
        )
    }

    @Test
    fun limits_match_limits_json() {
        val json = Json.parseToJsonElement(
            checkNotNull(javaClass.getResourceAsStream("/limits.json")) { "missing /limits.json" }
                .use { it.readBytes().decodeToString() },
        ).jsonObject
        val values: Map<String, Long> = json.getValue("limits").jsonArray.mapNotNull { entry ->
            val objectEntry: JsonObject = entry.jsonObject
            val name = (objectEntry.getValue("name") as JsonPrimitive).content
            if (name !in PHONE_LIMITS) return@mapNotNull null
            val value = (objectEntry.getValue("value") as JsonPrimitive).content
            name to (value.toLongOrNull() ?: error("limit $name is not an integer"))
        }.toMap()
        val expected = mapOf(
            "pairing_window_seconds" to limits.pairingWindowSeconds,
            "pairing_attempts_per_pairing_id" to limits.attemptsPerPairingId.toLong(),
            "pairing_rolling_attempts_interval_seconds" to limits.rollingAttemptsIntervalSeconds,
            "pairing_rolling_attempts_max" to limits.rollingAttemptsMax.toLong(),
            "spake_operations_in_flight" to limits.spakeOperationsInFlight.toLong(),
            "fresh_pa_per_connection" to limits.freshPaPerConnection.toLong(),
            "fresh_pa_rolling_interval_seconds" to limits.freshPaRollingIntervalSeconds,
            "fresh_pa_rolling_max" to limits.freshPaRollingMax.toLong(),
            "pairing_malformed_connections_rolling_max" to limits.malformedConnectionsRollingMax.toLong(),
            "pairing_malformed_rolling_interval_seconds" to limits.malformedRollingIntervalSeconds,
            "pairing_request_rolling_max" to limits.requestRollingMax.toLong(),
            "pairing_request_rolling_interval_seconds" to limits.requestRollingIntervalSeconds,
            "pairing_request_inactivity_timeout_seconds" to limits.requestInactivityTimeoutSeconds,
        )
        assertEquals("every phone pairing limit is mapped", PHONE_LIMITS, expected.keys)
        expected.forEach { (name, value) ->
            assertEquals("PairingLimits must match limits.json:$name", value, values[name])
        }
    }

    private companion object {
        const val CONN_ONE = 1L
        const val CONN_TWO = 2L
        const val FIFTH_INDEX = 4L
        const val MALFORMED_INDEX = 19L

        /** Every pairing limit the phone owns; any other limits.json entries are out of scope here. */
        val PHONE_LIMITS = setOf(
            "pairing_window_seconds",
            "pairing_attempts_per_pairing_id",
            "pairing_rolling_attempts_interval_seconds",
            "pairing_rolling_attempts_max",
            "spake_operations_in_flight",
            "fresh_pa_per_connection",
            "fresh_pa_rolling_interval_seconds",
            "fresh_pa_rolling_max",
            "pairing_malformed_connections_rolling_max",
            "pairing_malformed_rolling_interval_seconds",
            "pairing_request_rolling_max",
            "pairing_request_rolling_interval_seconds",
            "pairing_request_inactivity_timeout_seconds",
        )
    }
}
