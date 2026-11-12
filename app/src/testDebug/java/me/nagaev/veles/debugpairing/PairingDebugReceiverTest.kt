package me.nagaev.veles.debugpairing

import me.nagaev.veles.pairing.PairingWindow
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The scripted broadcasts open and close the phone's single window. Their ordered-broadcast
 * result is printed by `adb shell am broadcast`, so it must never carry the six-digit code:
 * the code is shown on the debug screen only.
 */
class PairingDebugReceiverTest {
    private val receiver = PairingDebugReceiver()

    @After
    fun detach() = PairingDebugHooks.detach()

    @Test
    fun broadcast_start_result_has_no_code() {
        PairingDebugHooks.attach(PairingWindow({ 1_000L }))
        val results = mutableListOf<String>()
        val logs = mutableListOf<String>()

        receiver.dispatch(PairingDebugReceiver.ACTION_PAIRING_START, { results += it }, { logs += it })

        val state = PairingDebugHooks.state.value
        assertTrue("the window is open", state.open)
        val code = requireNotNull(state.code) { "the screen shows the code" }
        assertTrue("the on-screen code is six digits", code.matches(Regex("\\d{6}")))
        assertEquals("the on-screen pairing id is 128 bits", 32, state.pairingId?.length)
        assertEquals("one ordered result", 1, results.size)
        assertEquals("one log line", 1, logs.size)
        assertFalse("the result never echoes the code", results.single().contains(code))
        assertFalse("the log never echoes the code", logs.single().contains(code))
        assertFalse("no six-digit run in the result", results.single().matches(Regex(".*\\d{6}.*")))
        assertFalse("no six-digit run in the log", logs.single().matches(Regex(".*\\d{6}.*")))
    }

    @Test
    fun broadcast_cancel_closes_window_without_echoing_code() {
        PairingDebugHooks.attach(PairingWindow({ 1_000L }))
        receiver.dispatch(PairingDebugReceiver.ACTION_PAIRING_START, {}, {})
        val code = requireNotNull(PairingDebugHooks.state.value.code)
        val results = mutableListOf<String>()
        val logs = mutableListOf<String>()

        receiver.dispatch(PairingDebugReceiver.ACTION_PAIRING_CANCEL, { results += it }, { logs += it })

        assertFalse("the window is closed", PairingDebugHooks.state.value.open)
        assertFalse("the result never echoes the code", results.single().contains(code))
        assertFalse("the log never echoes the code", logs.single().contains(code))
    }
}
