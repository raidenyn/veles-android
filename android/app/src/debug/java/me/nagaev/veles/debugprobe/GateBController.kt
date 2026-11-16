package me.nagaev.veles.debugprobe

/**
 * Debug-only Gate B controller (OTP-12 design D4/D5): the fixture command surface, kept beside
 * the pull router. Seed and disarm operate the fixture; only the four allowed triggers cancel a
 * schedule (a new schedule, disarm, an explicit cancel and detach), so a session close, a reseed
 * or a revocation leaves a pending push alone and records `skipped` if no session remains.
 */
internal class GateBController(
    private val fixture: GateBFixture,
    private val scheduler: GateBPushScheduler,
) {
    fun seed(lifetimes: List<Int>): Boolean = fixture.seed(lifetimes)

    fun disarm() {
        fixture.disarm()
        scheduler.cancel(GateBPushScheduler.REASON_DISARM)
    }

    fun schedule(delaySeconds: Long): Boolean = scheduler.schedule(delaySeconds)

    fun cancel() = scheduler.cancel(GateBPushScheduler.REASON_EXPLICIT)

    /** Service teardown: detach the fixture (which disarms it) and cancel/join the scheduler. */
    fun detach() {
        fixture.detach()
        scheduler.detach()
    }
}
