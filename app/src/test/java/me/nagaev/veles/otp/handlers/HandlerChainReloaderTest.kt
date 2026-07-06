package me.nagaev.veles.otp.handlers

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runTest
import me.nagaev.veles.otp.config.BankHandlerConfig
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class HandlerChainReloaderTest {

    private val notifier = object : OtpMessageHandler {
        override fun onOtpMessageReceived(message: OtpMessage) = Unit
    }

    private fun config(name: String, otp: String, money: String, merchant: String) =
        BankHandlerConfig(
            id = 0L,
            name = name,
            otpRegex = otp,
            moneyRegex = money,
            merchantRegex = merchant,
            createdAt = 0L,
            updatedAt = 0L,
        )

    private val bankA = config("BankA", """ SMS-(\w{3})-(\d{6}) """, """ pay ([A-Z]{3})(\d+\.\d{2}) """, """ at (\w+)""")
    private val bankB = config("BankB", """ CODE-(\w{3})-(\d{4}) """, """ pay ([A-Z]{3})(\d+\.\d{2}) """, """ at (\w+)""")

    private val msgA = Message("k", "src", "t", "Use SMS-OTP-123456 pay THB100.00 at SHOP")
    private val msgB = Message("k", "src", "t", "Use CODE-OTP-4321 pay USD50.00 at STORE")

    private fun reloader(flow: kotlinx.coroutines.flow.Flow<List<BankHandlerConfig>>) =
        HandlerChainReloader(flow, notifier)

    @Test
    fun `messageHandler defaults to an empty composite that filters everything before start`() {
        val r = reloader(MutableSharedFlow()) // never emits
        val result = r.messageHandler.onMessageReceived(msgA)
        assertEquals(MessageHandlingResult.FILTERED, result)
    }

    @Test
    fun `no emission yet - started collector keeps the empty default that filters everything`() = runTest(UnconfinedTestDispatcher()) {
        val neverEmits = MutableSharedFlow<List<BankHandlerConfig>>() // no replay, no initial value
        val r = reloader(neverEmits)
        r.start(this)
        try {
            assertEquals(MessageHandlingResult.FILTERED, r.messageHandler.onMessageReceived(msgA))
        } finally {
            r.stop()
        }
    }

    @Test
    fun `initial emission builds a chain that accepts a matching message`() = runTest(UnconfinedTestDispatcher()) {
        val flow = MutableStateFlow(listOf(bankA))
        val r = reloader(flow)
        r.start(this)
        try {
            val result = r.messageHandler.onMessageReceived(msgA)
            assertEquals(MessageHandlingResult.Status.ACCEPTED, result.status)
            assertEquals("BankA", result.matchedTemplateName)
        } finally {
            r.stop()
        }
    }

    @Test
    fun `changed config list is picked up - old message no longer matches, new one does`() = runTest(UnconfinedTestDispatcher()) {
        val flow = MutableStateFlow(listOf(bankA))
        val r = reloader(flow)
        r.start(this)
        try {
            // Before change: A matches, B does not.
            assertEquals(MessageHandlingResult.Status.ACCEPTED, r.messageHandler.onMessageReceived(msgA).status)
            assertEquals(MessageHandlingResult.Status.FILTERED, r.messageHandler.onMessageReceived(msgB).status)

            // Swap to BankB.
            flow.value = listOf(bankB)

            // After change: A no longer matches, B does.
            assertEquals(MessageHandlingResult.Status.FILTERED, r.messageHandler.onMessageReceived(msgA).status)
            assertEquals(MessageHandlingResult.Status.ACCEPTED, r.messageHandler.onMessageReceived(msgB).status)
            assertEquals("BankB", r.messageHandler.onMessageReceived(msgB).matchedTemplateName)
        } finally {
            r.stop()
        }
    }

    @Test
    fun `empty emission produces a chain that filters everything`() = runTest(UnconfinedTestDispatcher()) {
        val flow = MutableStateFlow(listOf(bankA))
        val r = reloader(flow)
        r.start(this)
        try {
            flow.value = emptyList()
            assertEquals(MessageHandlingResult.FILTERED, r.messageHandler.onMessageReceived(msgA))
        } finally {
            r.stop()
        }
    }

    @Test
    fun `malformed config does not kill the collector and a later fix takes effect`() = runTest(UnconfinedTestDispatcher()) {
        val flow = MutableStateFlow(listOf(bankA))
        val r = reloader(flow)
        r.start(this)
        try {
            assertEquals(MessageHandlingResult.Status.ACCEPTED, r.messageHandler.onMessageReceived(msgA).status)

            // Emit a config with an invalid regex; RegexMessageHandler construction throws, the
            // collector catches it and keeps the previous (BankA) chain.
            flow.value = listOf(config("Bad", "[", "x", "y"))
            assertEquals(
                "previous chain should still be active after a failed rebuild",
                MessageHandlingResult.Status.ACCEPTED,
                r.messageHandler.onMessageReceived(msgA).status,
            )

            // Emit a valid config; the collector recovers and the new chain takes effect.
            flow.value = listOf(bankB)
            assertEquals(MessageHandlingResult.Status.ACCEPTED, r.messageHandler.onMessageReceived(msgB).status)
            assertEquals("BankB", r.messageHandler.onMessageReceived(msgB).matchedTemplateName)
        } finally {
            r.stop()
        }
    }

    @Test
    fun `flow-level exception is logged and the collector restarts, picking up later emissions`() = runTest(UnconfinedTestDispatcher()) {
        // A cold flow that emits bankA on the first collection, then throws (simulating a
        // transient Room/cursor failure). On re-collection it emits bankB — proving the
        // collector restarted and picked up later emissions instead of dying permanently.
        var collectionCount = 0
        val flakyFlow = flow<List<BankHandlerConfig>> {
            collectionCount++
            if (collectionCount == 1) {
                emit(listOf(bankA))
                throw RuntimeException("simulated cursor failure")
            } else {
                emit(listOf(bankB))
            }
        }

        val r = HandlerChainReloader(flakyFlow, notifier, restartBackoffMs = 0L)
        r.start(this)
        try {
            // First collection: bankA is emitted, then the flow throws. The collector catches
            // it, logs, and restarts (backoff = 0 for the test). Second collection emits bankB.
            assertEquals(
                "chain should reflect the post-restart emission, proving the flow-level exception did not permanently kill the reloader",
                MessageHandlingResult.Status.ACCEPTED,
                r.messageHandler.onMessageReceived(msgB).status,
            )
            assertEquals("BankB", r.messageHandler.onMessageReceived(msgB).matchedTemplateName)
        } finally {
            r.stop()
        }
    }

    @Test
    fun `start cancels any prior collector so double-start does not orphan the old one`() = runTest(UnconfinedTestDispatcher()) {
        val flow = MutableStateFlow(listOf(bankA))
        val r = reloader(flow)
        r.start(this)
        val firstJob = r.job ?: error("first job should exist after start")
        assertTrue("first job should be active before second start", firstJob.isActive)

        // Second start should cancel the first collector, not orphan it.
        r.start(this)
        assertFalse("prior collector should be cancelled by second start", firstJob.isActive)
        assertTrue("prior collector should be marked cancelled", firstJob.isCancelled)

        // And the live (second) collector still drives updates from the shared flow.
        flow.value = listOf(bankB)
        assertEquals(MessageHandlingResult.Status.ACCEPTED, r.messageHandler.onMessageReceived(msgB).status)
        assertEquals("BankB", r.messageHandler.onMessageReceived(msgB).matchedTemplateName)
        r.stop()
    }

    @Test
    fun `stop cancels the restart loop - no further retries after stop`() = runTest(StandardTestDispatcher()) {
        var collectionCount = 0
        val alwaysFailing = flow<List<BankHandlerConfig>> {
            collectionCount++
            throw RuntimeException("always fails")
        }

        val r = HandlerChainReloader(alwaysFailing, notifier, restartBackoffMs = 1000L)
        r.start(this)

        // Advance virtual time enough to trigger a few retry cycles. Each collection throws,
        // the restart loop logs, delays 1000ms (virtual), then re-collects.
        advanceTimeBy(3500L)
        val countAfterRetries = collectionCount
        assertTrue("restart loop should have retried at least once before stop (got $countAfterRetries)", countAfterRetries >= 2)

        // stop() must cancel the collector so no further retries occur, even after more time passes.
        r.stop()
        advanceTimeBy(10000L)
        assertEquals("no further retries after stop", countAfterRetries, collectionCount)
    }
}
