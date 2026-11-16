package me.nagaev.veles.debugprobe

import kotlinx.coroutines.delay

/** The production step wait: a plain coroutine delay, re-checked against the injected clock. */
internal object CoroutineStepTimer : StepTimer {
    override suspend fun pause(millis: Long) = delay(millis)
}
