package me.nagaev.veles.debugprobe

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel
import me.nagaev.veles.trust.ActivationStep
import me.nagaev.veles.trust.InstallationIdentity
import me.nagaev.veles.trust.InstallationTrustRepository
import me.nagaev.veles.trust.PendingPeer
import me.nagaev.veles.trust.TrustSnapshot
import me.nagaev.veles.trust.TrustStorage
import me.nagaev.veles.trust.TrustedPeer
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The Gate B adb command surface (OTP-12 design D3/D4, spec "Debug-only five-record fixture" and
 * "Scheduled synthetic push"): the command layer only parses the `lifetimes` and `delay` extras
 * and forwards to the controller, so the fixture and the scheduler keep their own validation
 * messages. Every command returns one bounded structured result through the shell result channel
 * and writes exactly one bounded outcome line: the owning component's line for a real transition,
 * otherwise the command's own refusal or no-op line (never zero, never two from this layer).
 * Nothing here sleeps or reads a wall clock.
 */
class GateBCommandsTest {
    private val installationId = ByteArray(16) { (0x10 + it).toByte() }
    private val wallNow = 1_700_000_000_123L
    private val clock = ManualClock()

    /** Command-layer log lines; the owning components' outcome lines go to [runtimeLines]. */
    private val logs = mutableListOf<String>()
    private val results = mutableListOf<String>()
    private val runtimeLines = mutableListOf<String>()
    private val wake = RecordingWake()
    private val timer = PausedTimer(clock)
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
        log = { runtimeLines += it },
    )

    private fun controller(fixture: GateBFixture): GateBController = GateBController(
        fixture = fixture,
        scheduler = GateBPushScheduler(
            fixture = fixture,
            sessions = { NoSessions },
            clock = clock,
            timer = timer,
            wake = wake,
            // Unconfined so a schedule reaches its first step inside schedule(), as in production.
            scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined),
            processId = PROCESS_ID,
            log = { runtimeLines += it },
        ),
    )

    private fun commands(gateB: GateBController?): ProbeCommands = ProbeCommands(
        sessions = NoSessions,
        repository = UnusedRepository,
        injector = DebugTrustInjector(storage = UnusedStorage, elapsedMillis = { 0 }, wallMillis = { 0 }),
        handler = ProbeEventSource { SyntheticEvent("VAAAAA", ByteArray(16), ByteArray(0)) },
        log = { logs += it },
        result = { results += it },
        gateB = gateB,
        elapsed = ElapsedClock { COMMAND_ELAPSED },
    )

    @Test
    fun seed_parses_comma_list_and_reports_result() {
        val fixture = fixture()
        val commands = commands(controller(fixture))

        assertTrue(commands.gateBSeed("60,120,180,240,300"))

        assertTrue("a valid seed arms the fixture", fixture.armed)
        assertEquals("five records are seeded", 5, fixture.pullBody().first().toInt())
        assertEquals("the parsed list is reported", "gate-b seed ok lifetimes=60,120,180,240,300", results.single())
        assertTrue("the fixture owns the seed outcome line", runtimeLines.any { it.contains(" seeded count=5 ") })
        assertTrue("the command writes no second outcome line", logs.isEmpty())

        // A missing extra means the five-600 default, not a refusal.
        assertTrue(commands.gateBSeed(null))
        assertEquals(
            "the default is five 600 s lifetimes",
            "gate-b seed ok lifetimes=600,600,600,600,600",
            results.last(),
        )
        assertTrue("the command still writes no outcome line", logs.isEmpty())
    }

    @Test
    fun bad_lifetime_list_is_refused_and_changes_nothing() {
        val fixture = fixture()
        val commands = commands(controller(fixture))

        assertTrue(commands.gateBSeed("60,120,180,240,300"))
        val seeded = fixture.pullBody()

        assertFalse("the fixture refuses four values", commands.gateBSeed("60,120,180,240"))
        assertFalse("the fixture refuses an out-of-range value", commands.gateBSeed("0,120,180,240,300"))
        assertFalse("a non-numeric list is refused", commands.gateBSeed("60,120,180,240,soon"))
        assertFalse("an empty list is refused", commands.gateBSeed(""))
        assertFalse("a trailing comma is refused", commands.gateBSeed("60,120,180,240,300,"))
        assertFalse("a whitespace-only list is refused", commands.gateBSeed("   "))

        assertTrue("every refusal leaves the fixture armed", fixture.armed)
        assertArrayEquals("the seeded history is unchanged", seeded, fixture.pullBody())
        assertEquals(
            "each refusal reports one result",
            listOf(
                "gate-b seed ok lifetimes=60,120,180,240,300",
                "gate-b seed refused",
                "gate-b seed refused",
                "gate-b seed refused reason=lifetimes",
                "gate-b seed refused reason=lifetimes",
                "gate-b seed refused reason=lifetimes",
                "gate-b seed refused reason=lifetimes",
            ),
            results,
        )
        assertEquals(
            "only the malformed lists are logged by the command",
            List(4) { "elapsed=$COMMAND_ELAPSED gate-b seed refused reason=lifetimes" },
            logs,
        )
        assertEquals("the fixture logs the count refusal once", 1, runtimeLines.count { it.endsWith("refused seed reason=count") })
        assertEquals("the fixture logs the lifetime refusal once", 1, runtimeLines.count { it.endsWith("refused seed reason=lifetime") })
    }

    @Test
    fun seed_without_host_is_refused() {
        val commands = commands(gateB = null)

        assertFalse(commands.gateBSeed(null))

        assertEquals("nothing is reported beyond the refusal", listOf("gate-b seed refused reason=host"), results)
        assertEquals(
            "the no-service refusal is logged once with an elapsed timestamp",
            listOf("elapsed=$COMMAND_ELAPSED gate-b seed refused reason=host"),
            logs,
        )
    }

    /**
     * The seed eligibility is the fixture's own synchronized `detached` flag: a controller captured
     * before teardown cannot re-arm the fixture afterwards. No IO-pause seam is needed, because the
     * check and the teardown share one lock, so there is no interleaving left to test.
     */
    @Test
    fun seed_after_detach_is_refused() {
        val fixture = fixture()
        val controller = controller(fixture)
        val commands = commands(controller)

        controller.detach()
        assertFalse("a detached fixture refuses the seed", commands.gateBSeed(null))

        assertFalse("nothing is armed", fixture.armed)
        assertEquals("no record exists after the refused seed", 0, fixture.pullBody().first().toInt())
        assertEquals("one structured refusal", listOf("gate-b seed refused"), results)
        assertTrue("the command writes no second outcome line", logs.isEmpty())
        assertEquals(
            "the fixture logs exactly one refusal line",
            listOf("elapsed=0 refused seed reason=detached"),
            runtimeLines.filter { it.contains("refused") },
        )
        assertTrue("the refused seed never logs a seeded line", runtimeLines.none { it.contains(" seeded ") })
    }

    @Test
    fun schedule_requires_delay_extra() {
        val fixture = fixture()
        val commands = commands(controller(fixture))

        assertFalse("a missing delay is refused", commands.gateBSchedulePush(null))
        assertFalse("a blank delay is refused", commands.gateBSchedulePush(""))
        assertFalse("a fractional delay is refused", commands.gateBSchedulePush("1.5"))
        assertFalse("a non-numeric delay is refused", commands.gateBSchedulePush("soon"))
        assertFalse("an overflowing delay is refused", commands.gateBSchedulePush("99999999999999999999"))

        assertEquals(
            "every command-level refusal names the delay",
            List(5) { "gate-b schedule refused reason=delay" },
            results,
        )
        assertEquals(
            "the command logs each parse refusal once",
            List(5) { "elapsed=$COMMAND_ELAPSED gate-b schedule refused reason=delay" },
            logs,
        )
        assertTrue("nothing is scheduled", wake.acquires.isEmpty())

        // Parsed values reach the scheduler, which keeps its own messages and rules.
        assertFalse(commands.gateBSchedulePush("0"))
        assertFalse(commands.gateBSchedulePush("7201"))
        assertFalse("a negative delay is refused", commands.gateBSchedulePush("-5"))
        assertFalse("a valid delay still requires an armed fixture", commands.gateBSchedulePush("120"))

        assertEquals(
            "the scheduler's refusals are reported without a second message",
            listOf(
                "gate-b schedule refused delay=0",
                "gate-b schedule refused delay=7201",
                "gate-b schedule refused delay=-5",
                "gate-b schedule refused delay=120",
            ),
            results.takeLast(4),
        )
        assertEquals(
            "each out-of-range delay is refused by the scheduler",
            3,
            runtimeLines.count { it.endsWith("refused schedule reason=delay pid=$PROCESS_ID") },
        )
        assertEquals(
            "the valid delay reaches the armed check",
            1,
            runtimeLines.count { it.endsWith("refused schedule reason=unarmed pid=$PROCESS_ID") },
        )
        assertEquals("the command logs no owner-handled outcome", 5, logs.size)
        assertTrue("nothing is scheduled", wake.acquires.isEmpty())
    }

    @Test
    fun disarm_and_cancel_report_results() {
        val fixture = fixture()
        val commands = commands(controller(fixture))

        assertTrue(commands.gateBSeed(null))
        commands.gateBDisarm()

        assertFalse("disarm returns answering to the probe", fixture.armed)
        assertEquals("gate-b disarm ok", results.last())
        assertEquals("the fixture owns the disarmed line", 1, runtimeLines.count { it.endsWith(" disarmed") })
        assertTrue("the command writes no second outcome line", logs.isEmpty())

        assertTrue(commands.gateBSeed(null))
        assertTrue(commands.gateBSchedulePush("120"))

        assertEquals("gate-b schedule ok delay=120", results.last())
        assertEquals("the schedule holds the wake resource", 1, wake.acquires.size)
        assertEquals("the scheduler owns the scheduled line", 1, runtimeLines.count { it.contains(" scheduled delay=120 ") })

        commands.gateBCancelPush()

        assertEquals("gate-b cancel ok", results.last())
        assertEquals("cancel releases the wake resource", 1, wake.releases)
        assertTrue("the explicit cancellation is logged", runtimeLines.any { it.endsWith("cancelled reason=explicit pid=$PROCESS_ID") })
        assertTrue("cancel does not disarm the fixture", fixture.armed)
        assertTrue("the command writes no outcome line for owner-handled outcomes", logs.isEmpty())
    }

    @Test
    fun repeated_disarm_logs_one_outcome_each_time() {
        val fixture = fixture()
        val commands = commands(controller(fixture))

        assertTrue(commands.gateBSeed("60,120,180,240,300"))
        val seeded = fixture.pullBody()

        commands.gateBDisarm()

        assertFalse("the real disarm returns answering to the probe", fixture.armed)
        assertTrue("the fixture owns the real disarm line", logs.isEmpty())
        assertEquals("the fixture logs disarmed once", 1, runtimeLines.count { it.endsWith(" disarmed") })

        val afterDisarm = fixture.pullBody()
        assertArrayEquals("the history is still the seeded one", seeded, afterDisarm)

        commands.gateBDisarm()

        assertFalse("a repeated disarm changes no state", fixture.armed)
        assertArrayEquals("a no-op disarm does not disturb the fixture history", afterDisarm, fixture.pullBody())
        assertEquals(
            "the no-op disarm is the one observable line",
            listOf("elapsed=$COMMAND_ELAPSED gate-b disarm no-op"),
            logs,
        )
        assertEquals("a no-op disarm writes no fixture line", 1, runtimeLines.count { it.endsWith(" disarmed") })
        assertEquals(
            "both disarms still report the same result",
            listOf("gate-b seed ok lifetimes=60,120,180,240,300", "gate-b disarm ok", "gate-b disarm ok"),
            results,
        )
    }

    @Test
    fun cancel_without_pending_schedule_logs_one_outcome_each_time() {
        val fixture = fixture()
        val commands = commands(controller(fixture))

        commands.gateBCancelPush()

        assertEquals(listOf("gate-b cancel ok"), results)
        assertEquals(listOf("elapsed=$COMMAND_ELAPSED gate-b cancel no-op"), logs)
        assertTrue("no scheduler cancellation is logged", runtimeLines.none { it.contains("cancelled") })

        commands.gateBCancelPush()

        assertEquals(
            "each no-op cancel is exactly one line",
            List(2) { "elapsed=$COMMAND_ELAPSED gate-b cancel no-op" },
            logs,
        )
        assertEquals(listOf("gate-b cancel ok", "gate-b cancel ok"), results)
        assertTrue("still no scheduler cancellation", runtimeLines.none { it.contains("cancelled") })
        assertFalse("cancel does not arm the fixture", fixture.armed)
    }

    @Test
    fun new_strings_covered_by_release_guard() {
        val forbidden = releaseHookForbidden()
        val newStrings = listOf(
            ProbeReceiver.ACTION_GATE_B_SEED,
            ProbeReceiver.ACTION_GATE_B_DISARM,
            ProbeReceiver.ACTION_GATE_B_SCHEDULE_PUSH,
            ProbeReceiver.ACTION_GATE_B_CANCEL_PUSH,
            PowerManagerWakeResource.TAG,
        )

        val uncovered = newStrings.filter { value -> forbidden.none { needle -> needle in value } }

        assertTrue("not matched by releaseHookForbidden: $uncovered", uncovered.isEmpty())
    }

    /** The quoted needles of `releaseHookForbidden` in `build.gradle.kts`, as the guard reads them. */
    private fun releaseHookForbidden(): List<String> {
        val file = firstExisting("build.gradle.kts", "app/build.gradle.kts")
        val text = file.readText()
        val start = text.indexOf("val releaseHookForbidden = listOf(")
        assertTrue("releaseHookForbidden is declared in ${file.path}", start >= 0)
        val end = text.indexOf("\n)\n", start)
        assertTrue("releaseHookForbidden is a closed list", end > start)
        return Regex("\"([^\"\n]*)\"").findAll(text.substring(start, end)).map { it.groupValues[1] }.toList()
    }

    private fun firstExisting(vararg candidates: String): File = candidates
        .map(::File)
        .firstOrNull { it.isFile }
        ?: throw AssertionError("none of the expected files exists: ${candidates.joinToString()}")

    /** A hand-advanced elapsed-realtime clock; nothing here reads a wall clock. */
    private class ManualClock(var millis: Long = 0L) : ElapsedClock {
        override fun elapsedMillis(): Long = millis
    }

    /** Advances the injected clock by the requested step and then suspends until released. */
    private class PausedTimer(private val clock: ManualClock) : StepTimer {
        private val gate = Channel<Unit>(Channel.UNLIMITED)

        override suspend fun pause(millis: Long) {
            clock.millis += millis
            gate.receive()
        }
    }

    private class RecordingWake : WakeResource {
        val acquires = mutableListOf<Long>()
        var releases = 0

        override fun acquire(timeoutMillis: Long) {
            acquires += timeoutMillis
        }

        override fun release() {
            releases += 1
        }
    }

    private object UnusedStorage : TrustStorage {
        override suspend fun <T> transaction(block: (TrustSnapshot?) -> Pair<TrustSnapshot, T>): T = error("unused")

        override suspend fun reset() = Unit
    }

    private object UnusedRepository : InstallationTrustRepository {
        override suspend fun identity(): InstallationIdentity = error("unused")
        override suspend fun peers(): List<TrustedPeer> = error("unused")
        override suspend fun activePeers(): List<TrustedPeer> = error("unused")
        override suspend fun addPending(peer: PendingPeer) = error("unused")
        override suspend fun confirm(peerId: String, step: ActivationStep, proof: ByteArray) = error("unused")
        override suspend fun revoke(peerId: String) = error("unused")
        override suspend fun beginRevocation(peerId: String) = error("unused")
        override suspend fun endRevocation(peerId: String) = error("unused")
        override suspend fun markAuthenticated(peerId: String) = error("unused")
        override suspend fun resetIdentity() = error("unused")
    }

    private companion object {
        private const val PROCESS_ID = 4_242
        private const val COMMAND_ELAPSED = 7L
    }
}
