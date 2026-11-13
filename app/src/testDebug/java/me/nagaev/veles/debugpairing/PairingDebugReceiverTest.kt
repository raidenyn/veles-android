package me.nagaev.veles.debugpairing

import me.nagaev.veles.pairing.PairingWindow
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
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
    fun debug_state_to_string_redacts_code() {
        PairingDebugHooks.attach(PairingWindow({ 1_000L }))
        receiver.dispatch(PairingDebugReceiver.ACTION_PAIRING_START, {}, {})

        val state = PairingDebugHooks.state.value
        val code = requireNotNull(state.code)
        assertFalse("the data-class toString never carries the code", state.toString().contains(code))
        assertTrue("the redacted field is explicit", state.toString().contains("REDACTED"))
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

    @Test
    fun cancel_closes_window_and_invokes_the_attached_callback_once() {
        val window = PairingWindow({ 1_000L })
        var cancels = 0
        PairingDebugHooks.attach(window) { cancels += 1 }
        window.open()

        PairingDebugHooks.cancel()

        assertFalse("the debug cancel closes the window", window.isOpen())
        assertEquals("the enrollment cancel runs exactly once", 1, cancels)
    }

    @Test
    fun fingerprint_survives_the_window_closing_and_clears_on_a_new_attach() {
        val window = PairingWindow({ 1_000L })
        PairingDebugHooks.attach(window)
        PairingDebugHooks.start()
        PairingDebugHooks.publishFingerprint("ABCD EF01 2345")

        // OTP2-REC-011: the verified cB closes the window; the public value stays on screen.
        window.cancel()
        PairingDebugHooks.refresh()
        val closed = PairingDebugHooks.state.value
        assertFalse("the window is closed", closed.open)
        assertEquals("the fingerprint survives the window closing", "ABCD EF01 2345", closed.fingerprint)

        PairingDebugHooks.attach(PairingWindow({ 1_000L }))
        assertNull("a new window starts without a fingerprint", PairingDebugHooks.state.value.fingerprint)
    }
}
