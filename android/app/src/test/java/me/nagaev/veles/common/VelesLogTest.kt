package me.nagaev.veles.common

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VelesLogTest {

    private class RecordingLogSink : LogSink {
        val calls = mutableListOf<Pair<String, String>>()
        override fun d(tag: String, msg: String) {
            calls.add(tag to msg)
        }
    }

    private class MutableLogConfig(
        override var rawContentEnabled: Boolean = false,
    ) : LogConfig

    private val sink = RecordingLogSink()
    private val config = MutableLogConfig()
    private val title = "UOB"
    private val text = "Your OTP is 123456"
    private val otpValue = "123456"

    @Test
    fun `dNotificationPosted redacts to lengths when rawContentEnabled is false`() {
        val log = VelesLog(sink, config, debugEnabled = true)
        log.dNotificationLogged(pkg = "com.bank", title = title, text = text, key = "key1", postTime = 1000L)

        assertEquals(1, sink.calls.size)
        val (_, msg) = sink.calls[0]
        assertTrue("should contain titleLen", msg.contains("titleLen=${title.length}"))
        assertTrue("should contain textLen", msg.contains("textLen=${text.length}"))
        assertFalse("must not contain raw title", msg.contains(title))
        assertFalse("must not contain raw text", msg.contains(text))
    }

    @Test
    fun `dNotificationPosted logs raw content when rawContentEnabled is true`() {
        config.rawContentEnabled = true
        val log = VelesLog(sink, config, debugEnabled = true)
        log.dNotificationLogged(pkg = "com.bank", title = title, text = text, key = "key1", postTime = 1000L)

        assertEquals(1, sink.calls.size)
        val (_, msg) = sink.calls[0]
        assertTrue("should contain raw title", msg.contains(title))
        assertTrue("should contain raw text", msg.contains(text))
    }

    @Test
    fun `dCopiedOtp redacts to length when rawContentEnabled is false`() {
        val log = VelesLog(sink, config, debugEnabled = true)
        log.dCopiedOtp(otpValue)

        assertEquals(1, sink.calls.size)
        val (_, msg) = sink.calls[0]
        assertTrue("should contain len", msg.contains("len=${otpValue.length}"))
        assertFalse("must not contain the OTP value", msg.contains(otpValue))
    }

    @Test
    fun `dCopiedOtp logs raw value when rawContentEnabled is true`() {
        config.rawContentEnabled = true
        val log = VelesLog(sink, config, debugEnabled = true)
        log.dCopiedOtp(otpValue)

        assertEquals(1, sink.calls.size)
        val (_, msg) = sink.calls[0]
        assertTrue("should contain the OTP value", msg.contains(otpValue))
    }

    @Test
    fun `dNotificationPosted is a no-op when debugEnabled is false`() {
        val log = VelesLog(sink, config, debugEnabled = false)
        log.dNotificationLogged(pkg = "com.bank", title = title, text = text, key = "key1", postTime = 1000L)

        assertEquals(0, sink.calls.size)
    }

    @Test
    fun `dCopiedOtp is a no-op when debugEnabled is false`() {
        val log = VelesLog(sink, config, debugEnabled = false)
        log.dCopiedOtp(otpValue)

        assertEquals(0, sink.calls.size)
    }

    @Test
    fun `d forwards to sink when debugEnabled is true`() {
        val log = VelesLog(sink, config, debugEnabled = true)
        log.d("Tag", "non-sensitive message")

        assertEquals(1, sink.calls.size)
        assertEquals("Tag" to "non-sensitive message", sink.calls[0])
    }

    @Test
    fun `d is a no-op when debugEnabled is false`() {
        val log = VelesLog(sink, config, debugEnabled = false)
        log.d("Tag", "non-sensitive message")

        assertEquals(0, sink.calls.size)
    }
}
