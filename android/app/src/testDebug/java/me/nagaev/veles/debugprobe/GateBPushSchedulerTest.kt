package me.nagaev.veles.debugprobe

import android.os.PowerManager
import io.mockk.Runs
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.CancellationException
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
        val interruptedSends = AtomicInteger()
        val sessions = object : ProbeSessions {
            override fun openFullSessions(): List<ProbeSession> = listOf(session(1, 0x40))

            override suspend fun sendApplication(gen: ConnectionGeneration, type: Int, body: ByteArray): Boolean {
                sendStarted.complete(Unit)
                // Deliberately cancellable: the scheduler, not the destination, must keep an
                // admitted send's result wait alive across detach's cancellation.
                try {
                    sendGate.await()
                } catch (cancelled: CancellationException) {
                    interruptedSends.incrementAndGet()
                    throw cancelled
                }
                finishedSends.incrementAndGet()
                return true
            }
        }
        // detach delivers the scope cancellation before it releases the wake lock, so resolving
        // the admitted send from this hook proves the scheduler kept the result wait alive.
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
        assertEquals("an admitted send is never cancelled mid-wait", 0, interruptedSends.get())
        assertEquals(1, lines.count { it.contains("cancelled reason=detach") })

        // fail() and onDestroy() both reach shutdownSessions, so detach runs twice.
        scheduler.detach()

        assertEquals("a second detach logs no duplicate cancellation", 1, lines.count { it.contains("cancelled reason=detach") })
        assertEquals("the wake resource is released exactly once", 1, wake.releases)
        assertEquals("no wake lock is left held", 0, wake.held)
    }

    @Test
    fun cancel_at_the_fire_boundary_sends_nothing() {
        val clock = ManualClock()
        val lines = mutableListOf<String>()
        val timer = BoundaryTimer(clock)
        val sessions = RecordingSessions(listOf(session(1, 0x40)))
        val wake = RecordingWake()
        val fixture = fixture(clock, lines)
        val scheduler = scheduler(fixture, { sessions }, clock, timer, wake, lines)
        val controller = GateBController(fixture, scheduler)

        assertTrue(controller.schedule(1))
        // The worker is paused at the deadline, immediately before the deadline re-check.
        controller.cancel()
        timer.step()

        assertTrue("a cancelled schedule never begins a send", sessions.sends.isEmpty())
        assertEquals("no fired outcome", 0, lines.count { it.contains(" fired ") })
        assertEquals("no skipped outcome for a cancelled schedule", 0, lines.count { it.contains(" skipped") })
        assertEquals(1, lines.count { it.contains("cancelled reason=explicit") })
    }

    @Test
    fun replacement_at_the_fire_boundary_sends_only_the_replacement() {
        val clock = ManualClock()
        val lines = mutableListOf<String>()
        val timer = BoundaryTimer(clock)
        val sessions = RecordingSessions(listOf(session(1, 0x40)))
        val wake = RecordingWake()
        val fixture = fixture(clock, lines)
        val scheduler = scheduler(fixture, { sessions }, clock, timer, wake, lines)

        assertTrue(scheduler.schedule(1))
        // The old worker is paused at its deadline; the replacement retires it.
        assertTrue(scheduler.schedule(5))
        timer.step()

        assertEquals("the retired schedule begins no send", 0, sessions.sends.size)
        assertEquals(1, lines.count { it.contains("cancelled reason=replaced") })

        driveUntilOutcome(timer, lines)

        assertEquals("only the replacement fires", 1, sessions.sends.size)
        assertEquals(1, lines.count { it.contains(" fired ") })
    }

    @Test
    fun replacement_wake_lock_ownership_stays_with_the_replacement() {
        val clock = ManualClock()
        val lines = mutableListOf<String>()
        val timer = BoundaryTimer(clock)
        val sessions = RecordingSessions(listOf(session(1, 0x40)))
        val wake = RecordingWake()
        val fixture = fixture(clock, lines)
        val scheduler = scheduler(fixture, { sessions }, clock, timer, wake, lines)

        assertTrue(scheduler.schedule(5))
        assertTrue(scheduler.schedule(3))
        assertEquals("the replacement acquired exactly one lock", 1, wake.held)
        assertEquals("the retired attempt released its own lock", 1, wake.releases)

        // Resuming the retired attempt runs its cleanup; it must not release the replacement.
        timer.step()
        assertEquals("a retired attempt never releases the new acquisition", 1, wake.held)
        assertEquals("no release beyond the retired attempt's own", 1, wake.releases)

        driveUntilOutcome(timer, lines)

        assertEquals("the replacement fires once", 1, sessions.sends.size)
        assertEquals("the replacement released its own lock", 2, wake.releases)
        assertEquals("no wake lock is left held", 0, wake.held)
    }

    @Test
    fun detach_drains_a_predecessor_admitted_send() {
        val clock = ManualClock()
        val lines = mutableListOf<String>()
        val fixture = fixture(clock, lines)
        // A real dispatcher so the predecessor's admitted send genuinely outlives its job cancel.
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        val timer = SteppingTimer(clock)
        val sendStarted = CompletableDeferred<Unit>()
        val sendGate = CompletableDeferred<Unit>()
        val finishedSends = AtomicInteger()
        val interruptedSends = AtomicInteger()
        val sendCalls = AtomicInteger()
        val firstSend = AtomicBoolean(true)
        val sessions = object : ProbeSessions {
            override fun openFullSessions(): List<ProbeSession> = listOf(session(1, 0x40))

            override suspend fun sendApplication(gen: ConnectionGeneration, type: Int, body: ByteArray): Boolean {
                sendCalls.incrementAndGet()
                if (firstSend.compareAndSet(true, false)) {
                    sendStarted.complete(Unit)
                    try {
                        sendGate.await()
                    } catch (cancelled: CancellationException) {
                        interruptedSends.incrementAndGet()
                        throw cancelled
                    }
                    finishedSends.incrementAndGet()
                }
                return true
            }
        }
        var releases = 0
        // The replacement's retirement (release #2) resolves the predecessor's admitted send.
        val wake = RecordingWake {
            releases += 1
            if (releases == 2) sendGate.complete(Unit)
        }
        val scheduler = GateBPushScheduler(
            fixture = fixture,
            sessions = { sessions },
            clock = clock,
            timer = timer,
            wake = wake,
            scope = scope,
            processId = PROCESS_ID,
            log = { lines += it },
        )

        assertTrue(scheduler.schedule(1))
        timer.step()
        runBlocking { sendStarted.await() }
        assertEquals("the predecessor's send is in flight", 0, finishedSends.get())

        // Replacing retires the predecessor's job, but its admitted send keeps running.
        assertTrue(scheduler.schedule(5))
        assertEquals("the replacement acquired the wake lock", 1, wake.held)

        scheduler.detach()

        assertEquals("detach drained the predecessor's admitted send", 1, finishedSends.get())
        assertEquals("the predecessor's admitted send was not cancelled", 0, interruptedSends.get())
        assertEquals("the retired predecessor admitted exactly one send", 1, sendCalls.get())
        assertEquals("no wake lock is left held", 0, wake.held)
    }

    @Test
    fun cancel_during_an_admitted_send_stops_later_destinations() {
        val clock = ManualClock()
        val lines = mutableListOf<String>()
        val fixture = fixture(clock, lines)
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        val firstSendStarted = CompletableDeferred<Unit>()
        val releaseFirstSend = CompletableDeferred<Unit>()
        val sendCalls = AtomicInteger()
        val sessions = object : ProbeSessions {
            override fun openFullSessions(): List<ProbeSession> = listOf(session(1, 0x40), session(2, 0x50))

            override suspend fun sendApplication(gen: ConnectionGeneration, type: Int, body: ByteArray): Boolean {
                if (sendCalls.incrementAndGet() == 1) {
                    firstSendStarted.complete(Unit)
                    releaseFirstSend.await()
                }
                return true
            }
        }
        val wake = RecordingWake()
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
        runBlocking { firstSendStarted.await() }

        // The first destination's send is admitted; cancelling must stop the second one.
        scheduler.cancel("explicit")
        releaseFirstSend.complete(Unit)
        scheduler.detach()

        assertEquals("no destination after the admitted one is sent", 1, sendCalls.get())
        assertEquals("the admitted destination still logs one fired line", 1, lines.count { it.contains(" fired ") })
        assertEquals("the wake lock is released", 0, wake.held)
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
    private fun driveUntilOutcome(timer: StepDriver, lines: List<String>, maxSteps: Int = 16) {
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
    private class SteppingTimer(private val clock: ManualClock) :
        StepTimer,
        StepDriver {
        private val gate = Channel<Unit>(Channel.UNLIMITED)
        val pauses = mutableListOf<Long>()

        override suspend fun pause(millis: Long) {
            clock.millis += millis
            pauses += millis
            gate.receive()
        }

        override fun step() {
            gate.trySend(Unit)
        }
    }

    /**
     * A step timer whose suspension ignores cancellation, so a test can cancel a schedule while
     * the worker is paused immediately before the deadline re-check and then let that pause return
     * normally. This is the race the admission check and the attempt state must close.
     */
    private class BoundaryTimer(private val clock: ManualClock) :
        StepTimer,
        StepDriver {
        private val gate = Channel<Unit>(Channel.UNLIMITED)

        override suspend fun pause(millis: Long) {
            clock.millis += millis
            withContext(NonCancellable) { gate.receive() }
        }

        override fun step() {
            gate.trySend(Unit)
        }
    }

    private interface StepDriver {
        fun step()
    }

    private class RecordingWake(private val onRelease: () -> Unit = {}) : WakeResource {
        val acquires = mutableListOf<Long>()
        var releases = 0
        var held = 0

        override fun acquire(timeoutMillis: Long) {
            acquires += timeoutMillis
            held += 1
        }

        override fun release() {
            releases += 1
            held -= 1
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
