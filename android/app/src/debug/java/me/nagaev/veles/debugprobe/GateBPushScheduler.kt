package me.nagaev.veles.debugprobe

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import me.nagaev.veles.session.RecordType

/** One observable step wait; the production timer is a coroutine [kotlinx.coroutines.delay]. */
internal fun interface StepTimer {
    suspend fun pause(millis: Long)
}

/** The partial wake lock held while a Gate B push is scheduled; released on every exit. */
internal interface WakeResource {
    fun acquire(timeoutMillis: Long)

    fun release()
}

/**
 * Debug-only Gate B push scheduler (OTP-12 design D4 / spec "Scheduled synthetic push"):
 * exactly one scheduled push whose deadline is on the injected elapsed-realtime clock.
 *
 * Coroutine `delay` does not count deep sleep, so the wait is a stepped re-check: every step
 * wakes the loop and compares the clock with the deadline, and the partial wake lock keeps the
 * process able to run the step. The lock is acquired with a timed backstop of the delay plus
 * 60 s and released on every exit. A schedule is cancelled only by a new schedule, disarm, an
 * explicit cancel or detach; session close, reseed and revocation never cancel it.
 *
 * The at-deadline send uses the same `ProbeSessions` path as the adb push and one fresh fixture
 * event per open `full` session; no event joins the history. Every line carries the elapsed
 * timestamp and the process id and never a key or a full installation id.
 *
 * The scope is created by the caller (the debug session extension) on `Dispatchers.Default`,
 * never `Main`, because `detach()` runs `runBlocking` on the main thread during `onDestroy`.
 */
internal class GateBPushScheduler(
    private val fixture: GateBFixture,
    private val sessions: () -> ProbeSessions,
    private val clock: ElapsedClock,
    private val timer: StepTimer,
    private val wake: WakeResource,
    private val scope: CoroutineScope,
    private val processId: Int,
    private val log: (String) -> Unit,
) {
    private val lock = Any()
    private var job: Job? = null
    private var wakeHeld = false

    /** Validates a whole-second delay (1..7200) and the armed fixture, then replaces any schedule. */
    fun schedule(delaySeconds: Long): Boolean {
        if (delaySeconds < MIN_DELAY_SECONDS || delaySeconds > MAX_DELAY_SECONDS) {
            log(line("refused schedule reason=delay pid=$processId"))
            return false
        }
        if (!fixture.armed) {
            log(line("refused schedule reason=unarmed pid=$processId"))
            return false
        }
        val deadline = clock.elapsedMillis() + delaySeconds * MILLIS_PER_SECOND
        synchronized(lock) {
            cancelLocked(REASON_REPLACED)
            holdWakeLocked(delaySeconds * MILLIS_PER_SECOND + BACKSTOP_MILLIS)
            log(line("scheduled delay=$delaySeconds deadline=$deadline pid=$processId"))
            job = scope.launch { run(deadline) }
        }
        return true
    }

    /** Cancels the pending schedule (only detach, disarm, explicit cancel and replacement do). */
    fun cancel(reason: String) {
        synchronized(lock) { cancelLocked(reason) }
    }

    /**
     * Service teardown: cancels with reason `detach`, releases the wake resource, cancels the
     * scope and joins the scheduler job. The join is always reached, even for a completed job,
     * so an in-flight send cannot outlive the host. A second call is a no-op beyond the join.
     */
    fun detach() {
        val joinable = synchronized(lock) { cancelLocked(REASON_DETACH) }
        scope.cancel()
        runBlocking { joinable?.join() }
    }

    private suspend fun run(deadline: Long) {
        try {
            while (true) {
                val remaining = deadline - clock.elapsedMillis()
                if (remaining <= 0L) break
                timer.pause(if (remaining < STEP_MILLIS) remaining else STEP_MILLIS)
            }
            fire()
        } finally {
            dropWake()
        }
    }

    private suspend fun fire() {
        val target = sessions()
        val destinations = target.openFullSessions()
        if (destinations.isEmpty()) {
            log(line("skipped pid=$processId"))
            return
        }
        destinations.forEach { session ->
            val event = fixture.nextPush()
            if (target.sendApplication(session.generation, RecordType.PUSH_EVENT.wire, event.body)) {
                log(line("fired gen=${session.generation.serial} session=${hex(session.sessionIdPrefix)} pid=$processId"))
            } else {
                log(line("push refused gen=${session.generation.serial} pid=$processId"))
            }
        }
    }

    /** Logs only a real transition, so a completed or already-cancelled schedule stays silent. */
    private fun cancelLocked(reason: String): Job? {
        val running = job
        dropWakeLocked()
        if (running != null && running.isActive) {
            log(line("cancelled reason=$reason pid=$processId"))
            running.cancel()
        }
        return running
    }

    private fun holdWakeLocked(timeoutMillis: Long) {
        if (wakeHeld) return
        wakeHeld = true
        wake.acquire(timeoutMillis)
    }

    private fun dropWake() {
        synchronized(lock) { dropWakeLocked() }
    }

    private fun dropWakeLocked() {
        if (!wakeHeld) return
        wakeHeld = false
        wake.release()
    }

    private fun line(message: String): String = "elapsed=${clock.elapsedMillis()} $message"

    internal companion object {
        internal const val MIN_DELAY_SECONDS = 1L
        internal const val MAX_DELAY_SECONDS = 7_200L
        internal const val STEP_MILLIS = 1_000L
        internal const val BACKSTOP_MILLIS = 60_000L
        internal const val MILLIS_PER_SECOND = 1_000L

        internal const val REASON_REPLACED = "replaced"
        internal const val REASON_DISARM = "disarm"
        internal const val REASON_EXPLICIT = "explicit"
        internal const val REASON_DETACH = "detach"
    }
}
