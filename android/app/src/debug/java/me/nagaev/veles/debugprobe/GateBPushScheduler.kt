package me.nagaev.veles.debugprobe

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
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
 * Each schedule is its own [Attempt]. One lock serialises (a) a cancel or replacement marking the
 * attempt inactive, (b) the active check immediately before a send is admitted and (c) wake-lock
 * ownership, so a retired attempt can never send and its late cleanup can never release a newer
 * attempt's wake lock. Once a send is admitted, its `sendApplication` result wait runs
 * non-cancellably: the host has already accepted the record and detach must be able to await it.
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
    /** One schedule attempt: its own liveness and the wake lock it owns while it is current. */
    private class Attempt(val deadlineMillis: Long) {
        var active = true
        var job: Job? = null
    }

    private val lock = Any()

    /** The attempt that may still fire; the others are retired and can never send. */
    private var current: Attempt? = null

    /** Every launched attempt, so detach can join predecessors and the latest alike. */
    private val jobs = mutableSetOf<Job>()

    /** The attempt holding [wake]; only this attempt may release it. */
    private var wakeOwner: Attempt? = null

    private var detached = false

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
        val launched = synchronized(lock) {
            if (detached) {
                log(line("refused schedule reason=detached pid=$processId"))
                return false
            }
            current?.let { retireLocked(it, REASON_REPLACED) }
            val attempt = Attempt(deadline)
            current = attempt
            acquireWakeLocked(attempt, delaySeconds * MILLIS_PER_SECOND + BACKSTOP_MILLIS)
            log(line("scheduled delay=$delaySeconds deadline=$deadline pid=$processId"))
            registerLocked(attempt)
        }
        // Started outside the lock: an unconfined start may run to the first suspension inline.
        launched.start()
        return true
    }

    /** Cancels the pending schedule (only detach, disarm, explicit cancel and replacement do). */
    fun cancel(reason: String) {
        synchronized(lock) { current?.let { retireLocked(it, reason) } }
    }

    /**
     * Service teardown: retires the current attempt with reason `detach`, cancels the scope and
     * joins **every** outstanding attempt, including predecessors kept alive by a cancelled send.
     * A second call only cancels/joins what is left. No attempt, admitted send or cleanup can
     * outlive this call.
     */
    fun detach() {
        val outstanding = synchronized(lock) {
            val attempt = current
            current = null
            // Mark and log the cancellation first, but release the wake lock only after the
            // scope cancel: an in-flight send is resolved by that release, and the cancel must
            // have been delivered first so the non-cancellable admission is what keeps it alive.
            attempt?.let { retireLocked(it, REASON_DETACH, releaseWake = false) }
            scope.cancel()
            attempt?.let { releaseWakeLocked(it) }
            detached = true
            jobs.toList()
        }
        runBlocking { outstanding.forEach { it.join() } }
    }

    private suspend fun run(attempt: Attempt) {
        try {
            while (true) {
                if (!isActive(attempt)) return
                val remaining = attempt.deadlineMillis - clock.elapsedMillis()
                if (remaining <= 0L) break
                timer.pause(if (remaining < STEP_MILLIS) remaining else STEP_MILLIS)
            }
            fire(attempt)
        } finally {
            synchronized(lock) { releaseWakeLocked(attempt) }
        }
    }

    private suspend fun fire(attempt: Attempt) {
        val target = sessions()
        val destinations = target.openFullSessions()
        if (destinations.isEmpty()) {
            synchronized(lock) {
                if (!attempt.active) return
                log(line("skipped pid=$processId"))
                attempt.active = false
            }
            return
        }
        for (session in destinations) {
            // Admission and the cancel/replace check share the lock with the state flip: a
            // cancellation that wins here prevents the send, one that loses is after admission.
            val event = synchronized(lock) {
                if (!attempt.active) return
                fixture.nextPush()
            }
            // The host has accepted an admitted send; its result wait must not be interrupted,
            // or detach could return while the host operation is still unresolved.
            val sent = withContext(NonCancellable) {
                target.sendApplication(session.generation, RecordType.PUSH_EVENT.wire, event.body)
            }
            if (sent) {
                log(line("fired gen=${session.generation.serial} session=${hex(session.sessionIdPrefix)} pid=$processId"))
            } else {
                log(line("push refused gen=${session.generation.serial} pid=$processId"))
            }
        }
        synchronized(lock) { attempt.active = false }
    }

    private fun isActive(attempt: Attempt): Boolean = synchronized(lock) { attempt.active }

    private fun registerLocked(attempt: Attempt): Job {
        val job = scope.launch(start = CoroutineStart.LAZY) { run(attempt) }
        attempt.job = job
        jobs += job
        job.invokeOnCompletion {
            synchronized(lock) {
                attempt.active = false
                if (current === attempt) current = null
                jobs -= job
                releaseWakeLocked(attempt)
            }
        }
        return job
    }

    /** Marks the attempt dead and releases only the wake lock that attempt actually owns. */
    private fun retireLocked(attempt: Attempt, reason: String, releaseWake: Boolean = true) {
        if (!attempt.active) return
        attempt.active = false
        log(line("cancelled reason=$reason pid=$processId"))
        if (releaseWake) releaseWakeLocked(attempt)
        attempt.job?.cancel()
    }

    private fun acquireWakeLocked(attempt: Attempt, timeoutMillis: Long) {
        // Owner only after a successful acquire: a failed acquire must never be released.
        wake.acquire(timeoutMillis)
        wakeOwner = attempt
    }

    /** A retired attempt's late cleanup is a no-op once a newer attempt owns the lock. */
    private fun releaseWakeLocked(attempt: Attempt) {
        if (wakeOwner !== attempt) return
        wakeOwner = null
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
