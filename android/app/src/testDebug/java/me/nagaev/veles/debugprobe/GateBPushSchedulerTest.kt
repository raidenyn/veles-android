package me.nagaev.veles.debugprobe

import android.os.PowerManager
import io.mockk.Runs
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import me.nagaev.veles.session.RecordType
import me.nagaev.veles.sharing.ConnectionGeneration
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/**
 * Debug-only Gate B push scheduler (OTP-12 design D4 / spec "Scheduled synthetic push" and
 * "Cancellation and detach"). The clock, the step timer and the wake resource are injected; the
 * fake timer advances the manual clock by the requested step and then suspends until the test
 * releases that step, so nothing here sleeps or reads a wall clock.
 */
class GateBPushSchedulerTest {
    private val installationId = ByteArray(16) { (0x10 + it).toByte() }
    private val wallNow = 1_700_000_000_123L
    private var entropyCursor = 0

    /** Deterministic entropy: every synthetic push gets a distinct event id. */
    private val random: (Int) -> ByteArray = { size ->
        ByteArray(size) { ((entropyCursor + it) and 0xFF).toByte() }.also { entropyCursor += 7 }
    }

    private fun fixture(clock: ElapsedClock, lines: MutableList<String>): GateBFixture = GateBFixture(
        localInstallationId = installationId,
        elapsed = clock,
        wallMillis = { wallNow },
        random = random,
        log = { lines += it },
    ).also { assertTrue("the test fixture is armed", it.seed(List(5) { 600 })) }

    private fun unarmedFixture(clock: ElapsedClock, lines: MutableList<String>): GateBFixture = GateBFixture(
        localInstallationId = installationId,
        elapsed = clock,
        wallMillis = { wallNow },
        random = random,
        log = { lines += it },
    )

    private fun scope(): CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)

    private fun scheduler(
        fixture: GateBFixture,
        sessions: () -> ProbeSessions,
        clock: ElapsedClock,
        timer: StepTimer,
        wake: WakeResource,
        lines: MutableList<String>,
        scope: CoroutineScope = scope(),
        processId: Int = PROCESS_ID,
    ): GateBPushScheduler = GateBPushScheduler(
        fixture = fixture,
        sessions = sessions,
        clock = clock,
        timer = timer,
        wake = wake,
        scope = scope,
        processId = processId,
        log = { lines += it },
    )

    @Test
    fun no_push_one_second_before_deadline() {
        val clock = ManualClock()
        val lines = mutableListOf<String>()
        val timer = SteppingTimer(clock)
        val sessions = RecordingSessions(listOf(session(1, 0x40)))
        val scheduler = scheduler(fixture(clock, lines), { sessions }, clock, timer, RecordingWake(), lines)

        assertTrue(scheduler.schedule(2))

        assertEquals("the first step lands one second before the deadline", 1_000L, clock.millis)
        assertTrue("no push before the deadline", sessions.sends.isEmpty())
        assertEquals("nothing is logged as fired", 0, lines.count { it.contains(" fired ") })

        timer.step()
        assertEquals("the step resumes at the deadline", 2_000L, clock.millis)
        assertTrue("the push waits for the deadline re-check", sessions.sends.isEmpty())

        timer.step()

        assertEquals("the push fires once the clock reaches the deadline", 1, sessions.sends.size)
        assertEquals(1, lines.count { it.contains(" fired ") })
    }

    @Test
    fun fires_at_deadline_to_each_open_full_session() {
        val clock = ManualClock()
        val lines = mutableListOf<String>()
        val timer = SteppingTimer(clock)
        val first = session(1, 0x40)
        val second = session(2, 0x50)
        val sessions = RecordingSessions(listOf(first, second))
        val wake = RecordingWake()
        val fixture = fixture(clock, lines)
        val scheduler = scheduler(fixture, { sessions }, clock, timer, wake, lines)

        assertTrue(scheduler.schedule(1))
        assertTrue("nothing fires inside the step that reaches the deadline", sessions.sends.isEmpty())
        timer.step()

        assertEquals("one fresh push per open full session", 2, sessions.sends.size)
        assertEquals(
            "both destinations receive a push_event",
            listOf(RecordType.PUSH_EVENT.wire, RecordType.PUSH_EVENT.wire),
            sessions.sends.map { it.type },
        )
        val fired = lines.filter { it.contains(" fired ") }
        assertEquals("one fired line per destination", 2, fired.size)
        assertTrue("the first destination is named by its session prefix", fired[0].contains("session=${hex(first.sessionIdPrefix)}"))
        assertTrue("the second destination is named by its session prefix", fired[1].contains("session=${hex(second.sessionIdPrefix)}"))
        assertEquals("a pushed event never joins the history", 5, fixture.pullBody().first().toInt())
        assertEquals("the wake resource is released on fire", 1, wake.releases)
    }

    @Test
    fun skipped_when_no_session() {
        val clock = ManualClock()
        val lines = mutableListOf<String>()
        val timer = SteppingTimer(clock)
        val sessions = RecordingSessions(emptyList())
        val wake = RecordingWake()
        val scheduler = scheduler(fixture(clock, lines), { sessions }, clock, timer, wake, lines)

        assertTrue(scheduler.schedule(1))
        timer.step()

        assertTrue("nothing is sent", sessions.sends.isEmpty())
        assertEquals("the outcome is logged as skipped", 1, lines.count { it.contains(" skipped") })
        assertTrue(lines.any { it.startsWith("elapsed=1000 skipped") })
        assertEquals("the wake resource is released on skip", 1, wake.releases)
    }

    @Test
    fun delay_0_negative_and_7201_refused() {
        val clock = ManualClock()
        val lines = mutableListOf<String>()
        val timer = SteppingTimer(clock)
        val sessions = RecordingSessions(listOf(session(1, 0x40)))
        val wake = RecordingWake()
        val scheduler = scheduler(fixture(clock, lines), { sessions }, clock, timer, wake, lines)

        listOf(0L, -1L, 7_201L).forEach { delay ->
            assertFalse("delay $delay is refused", scheduler.schedule(delay))
        }

        assertEquals("one refusal line per bad delay", 3, lines.count { it.contains("refused schedule reason=delay") })
        assertTrue("nothing is scheduled", lines.none { it.contains(" scheduled ") })
        assertEquals("no timer step ran", emptyList<Long>(), timer.pauses)
        assertTrue("no wake lock is taken", wake.acquires.isEmpty())
    }

    @Test
    fun unarmed_refused() {
        val clock = ManualClock()
        val lines = mutableListOf<String>()
        val timer = SteppingTimer(clock)
        val sessions = RecordingSessions(listOf(session(1, 0x40)))
        val wake = RecordingWake()
        val scheduler = scheduler(unarmedFixture(clock, lines), { sessions }, clock, timer, wake, lines)

        assertFalse(scheduler.schedule(1))

        assertTrue(lines.any { it.contains("refused schedule reason=unarmed") })
        assertTrue("nothing is scheduled", lines.none { it.contains(" scheduled ") })
        assertTrue("no timer step ran", timer.pauses.isEmpty())
        assertTrue("no wake lock is taken", wake.acquires.isEmpty())
    }

    @Test
    fun second_schedule_replaces_first_and_is_logged() {
        val clock = ManualClock()
        val lines = mutableListOf<String>()
        val timer = SteppingTimer(clock)
        val sessions = RecordingSessions(listOf(session(1, 0x40)))
        val wake = RecordingWake()
        val scheduler = scheduler(fixture(clock, lines), { sessions }, clock, timer, wake, lines)

        assertTrue(scheduler.schedule(5))
        assertTrue(scheduler.schedule(3))

        assertEquals("the replacement is logged as a cancel", 1, lines.count { it.contains("cancelled reason=replaced") })
        assertEquals("both schedules log the scheduled outcome", 2, lines.count { it.contains(" scheduled ") })

        driveUntilOutcome(timer, lines)

        assertEquals("only the replacement fires", 1, sessions.sends.size)
        assertEquals(1, lines.count { it.contains(" fired ") })
    }

    @Test
    fun explicit_cancel_logged() {
        val clock = ManualClock()
        val lines = mutableListOf<String>()
        val timer = SteppingTimer(clock)
        val sessions = RecordingSessions(listOf(session(1, 0x40)))
        val wake = RecordingWake()
        val fixture = fixture(clock, lines)
        val scheduled = scheduler(fixture, { sessions }, clock, timer, wake, lines)
        val controller = GateBController(fixture, scheduled)

        assertTrue(controller.schedule(5))
        controller.cancel()

        assertEquals(1, lines.count { it.contains("cancelled reason=explicit") })
        assertEquals("the wake resource is released on cancel", 1, wake.releases)
        driveUntilOutcome(timer, lines)
        assertTrue("a cancelled schedule never fires", sessions.sends.isEmpty())
        assertEquals(0, lines.count { it.contains(" fired ") })
        assertEquals(0, lines.count { it.contains(" skipped") })
    }

    @Test
    fun session_close_reseed_and_revocation_do_not_cancel() {
        val clock = ManualClock()
        val lines = mutableListOf<String>()
        val timer = SteppingTimer(clock)
        val closed = session(1, 0x40)
        val open = session(2, 0x50)
        val sessions = RecordingSessions(listOf(closed, open))
        val wake = RecordingWake()
        val fixture = fixture(clock, lines)
        val scheduled = scheduler(fixture, { sessions }, clock, timer, wake, lines)
        val controller = GateBController(fixture, scheduled)

        assertTrue(controller.schedule(2))

        // A protected session closes and the fixture is reseeded; a revocation closes sessions
        // through the host, so the surviving session is all the deadline sees.
        sessions.open = listOf(open)
        assertTrue(controller.seed(List(5) { 600 }))
        assertEquals("none of those triggers cancels the schedule", 0, lines.count { it.contains(" cancelled ") })

        driveUntilOutcome(timer, lines)

        assertEquals("the schedule still fires to the open session", 1, sessions.sends.size)
        assertEquals(1, lines.count { it.contains(" fired ") })
        assertTrue("the reseed replaced only the history", lines.any { it.contains("seeded") })
    }

    @Test
    fun disarm_cancels() {
        val clock = ManualClock()
        val lines = mutableListOf<String>()
        val timer = SteppingTimer(clock)
        val sessions = RecordingSessions(listOf(session(1, 0x40)))
        val wake = RecordingWake()
        val fixture = fixture(clock, lines)
        val scheduled = scheduler(fixture, { sessions }, clock, timer, wake, lines)
        val controller = GateBController(fixture, scheduled)

        assertTrue(controller.schedule(5))
        controller.disarm()

        assertFalse("the fixture is disarmed", fixture.armed)
        assertTrue(lines.any { it.contains("disarmed") })
        assertEquals(1, lines.count { it.contains("cancelled reason=disarm") })
        assertEquals("the wake resource is released on disarm", 1, wake.releases)
        driveUntilOutcome(timer, lines)
        assertTrue("a disarmed schedule never fires", sessions.sends.isEmpty())
    }

    @Test
    fun wake_resource_released_on_fire_skip_cancel_and_detach() {
        assertEquals("fire releases the wake resource", 1, wakeReleases(fire = true))
        assertEquals("skip releases the wake resource", 1, wakeReleases(fire = false))
        assertEquals("cancel releases the wake resource", 1, wakeReleases(cancel = true))
        assertEquals("detach releases the wake resource", 1, wakeReleases(detach = true))
    }

    @Test
    fun backstop_timeout_is_delay_plus_60_seconds() {
        val clock = ManualClock()
        val lines = mutableListOf<String>()
        val timer = SteppingTimer(clock)
        val sessions = RecordingSessions(listOf(session(1, 0x40)))
        val wake = RecordingWake()
        val scheduler = scheduler(fixture(clock, lines), { sessions }, clock, timer, wake, lines)

        assertTrue(scheduler.schedule(7_200))

        assertEquals(listOf(7_200_000L + 60_000L), wake.acquires)
        scheduler.cancel("explicit")
    }

    @Test
    fun scheduled_and_fired_lines_carry_the_same_process_id_and_elapsed_timestamps() {
        val clock = ManualClock()
        val lines = mutableListOf<String>()
        val timer = SteppingTimer(clock)
        val sessions = RecordingSessions(listOf(session(1, 0x40)))
        val scheduler = scheduler(fixture(clock, lines), { sessions }, clock, timer, RecordingWake(), lines)

        assertTrue(scheduler.schedule(1))
        driveUntilOutcome(timer, lines)

        val scheduled = lines.first { it.contains(" scheduled ") }
        val fired = lines.first { it.contains(" fired ") }
        assertTrue("the scheduled line carries the process id", scheduled.contains("pid=$PROCESS_ID"))
        assertTrue("the fired line carries the same process id", fired.contains("pid=$PROCESS_ID"))
        assertTrue(scheduled.startsWith("elapsed="))
        assertTrue(fired.startsWith("elapsed="))
        val startedAt = scheduled.substringAfter("elapsed=").substringBefore(" ").toLong()
        val firedAt = fired.substringAfter("elapsed=").substringBefore(" ").toLong()
        assertTrue("the push never fires before its deadline", firedAt >= startedAt + 1_000L)
    }

    @Test
    fun scheduler_log_lines_have_no_key_or_full_installation_id() {
        val clock = ManualClock()
        val lines = mutableListOf<String>()
        val timer = SteppingTimer(clock)
        val sessions = RecordingSessions(listOf(session(1, 0x40)))
        val scheduler = scheduler(fixture(clock, lines), { sessions }, clock, timer, RecordingWake(), lines)

        assertTrue(scheduler.schedule(1))
        driveUntilOutcome(timer, lines)

        assertTrue("the schedule produced lines", lines.isNotEmpty())
        for (line in lines) {
            assertFalse("no full installation id in '$line'", line.contains(hex(installationId)))
            assertFalse("no key material in '$line'", line.contains("key="))
        }
    }

    @Test
    fun detach_joins_an_in_flight_send_and_is_idempotent() {
        val clock = ManualClock()
        val lines = mutableListOf<String>()
        val fixture = fixture(clock, lines)
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        val sendStarted = CompletableDeferred<Unit>()
        val sendGate = CompletableDeferred<Unit>()
        val finishedSends = AtomicInteger()
        val sessions = object : ProbeSessions {
            override fun openFullSessions(): List<ProbeSession> = listOf(session(1, 0x40))

            override suspend fun sendApplication(gen: ConnectionGeneration, type: Int, body: ByteArray): Boolean {
                sendStarted.complete(Unit)
                // A real send is not interrupted by the scheduler's cancellation; detach must
                // wait for it to finish instead of racing the host shutdown.
                withContext(NonCancellable) { sendGate.await() }
                finishedSends.incrementAndGet()
                return true
            }
        }
        // detach drops the wake resource before joining; completing the in-flight send from that
        // observable point means the join, not a real wait, is what makes the assertion hold.
        val wake = RecordingWake { sendGate.complete(Unit) }
        val scheduler = GateBPushScheduler(
            fixture = fixture,
            sessions = { sessions },
            clock = clock,
            timer = StepTimer { millis -> clock.millis += millis },
            wake = wake,
            scope = scope,
            processId = PROCESS_ID,
            log = { lines += it },
        )

        assertTrue(scheduler.schedule(1))
        runBlocking { sendStarted.await() }
        assertEquals("the send is in flight", 0, finishedSends.get())

        scheduler.detach()

        assertEquals("detach returned only after the in-flight send completed", 1, finishedSends.get())
        assertEquals(1, lines.count { it.contains("cancelled reason=detach") })

        // fail() and onDestroy() both reach shutdownSessions, so detach runs twice.
        scheduler.detach()

        assertEquals("a second detach logs no duplicate cancellation", 1, lines.count { it.contains("cancelled reason=detach") })
        assertEquals("the wake resource is released exactly once", 1, wake.releases)
    }

    @Test
    fun wake_resource_release_after_backstop_expiry_is_safe() {
        val lock = mockk<PowerManager.WakeLock>()
        val held = AtomicBoolean(false)
        every { lock.setReferenceCounted(false) } just Runs
        every { lock.isHeld } answers { held.get() }
        every { lock.acquire(any()) } answers { held.set(true) }
        every { lock.release() } answers { held.set(false) }

        val resource = PowerManagerWakeResource(lock)

        verify(exactly = 1) { lock.setReferenceCounted(false) }
        resource.acquire(65_000)
        verify(exactly = 1) { lock.acquire(65_000) }

        // The timed acquire expired on its own: releasing now must not trigger an under-lock.
        held.set(false)
        resource.release()
        verify(exactly = 0) { lock.release() }

        held.set(true)
        resource.release()
        verify(exactly = 1) { lock.release() }
        resource.release()
        verify(exactly = 1) { lock.release() }
    }

    /** Builds a fresh scheduler for one terminal outcome and returns how often the wake was released. */
    private fun wakeReleases(fire: Boolean = false, cancel: Boolean = false, detach: Boolean = false): Int {
        val clock = ManualClock()
        val lines = mutableListOf<String>()
        val timer = SteppingTimer(clock)
        val sessions = RecordingSessions(if (fire) listOf(session(1, 0x40)) else emptyList())
        val wake = RecordingWake()
        val fixture = fixture(clock, lines)
        val scheduler = scheduler(fixture, { sessions }, clock, timer, wake, lines)
        val controller = GateBController(fixture, scheduler)

        assertTrue(controller.schedule(5))
        when {
            cancel -> controller.cancel()
            detach -> controller.detach()
            else -> driveUntilOutcome(timer, lines)
        }
        if (fire) {
            assertEquals("the push fired", 1, sessions.sends.size)
        } else if (!cancel && !detach) {
            assertEquals("the push was skipped", 1, lines.count { it.contains(" skipped") })
        }
        return wake.releases
    }

    /** Releases the fake timer step by step until a terminal outcome is logged. */
    private fun driveUntilOutcome(timer: SteppingTimer, lines: List<String>, maxSteps: Int = 16) {
        var steps = 0
        while (steps < maxSteps && lines.none { it.contains(" fired ") || it.contains(" skipped") }) {
            timer.step()
            steps += 1
        }
    }

    private fun session(serial: Long, fill: Int): ProbeSession = ProbeSession(
        generation = ConnectionGeneration("aa", serial),
        sessionIdPrefix = ByteArray(8) { (fill + it).toByte() },
    )

    /** A hand-advanced elapsed-realtime clock; nothing here reads a wall clock. */
    private class ManualClock(var millis: Long = 0L) : ElapsedClock {
        override fun elapsedMillis(): Long = millis
    }

    /**
     * Advances the injected clock by the requested step and then suspends (yielding the thread)
     * until the test releases that step, so each loop iteration is observable without sleeping.
     */
    private class SteppingTimer(private val clock: ManualClock) : StepTimer {
        private val gate = Channel<Unit>(Channel.UNLIMITED)
        val pauses = mutableListOf<Long>()

        override suspend fun pause(millis: Long) {
            clock.millis += millis
            pauses += millis
            gate.receive()
        }

        fun step() {
            gate.trySend(Unit)
        }
    }

    private class RecordingWake(private val onRelease: () -> Unit = {}) : WakeResource {
        val acquires = mutableListOf<Long>()
        var releases = 0

        override fun acquire(timeoutMillis: Long) {
            acquires += timeoutMillis
        }

        override fun release() {
            releases += 1
            onRelease()
        }
    }

    private class RecordingSessions(sessions: List<ProbeSession>) : ProbeSessions {
        var open: List<ProbeSession> = sessions
        val sends = mutableListOf<Send>()

        override fun openFullSessions(): List<ProbeSession> = open

        override suspend fun sendApplication(gen: ConnectionGeneration, type: Int, body: ByteArray): Boolean {
            sends += Send(gen, type)
            return true
        }
    }

    private data class Send(val generation: ConnectionGeneration, val type: Int)

    private companion object {
        private const val PROCESS_ID = 4_242
    }
}
