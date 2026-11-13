package me.nagaev.veles.pairing

import me.nagaev.veles.session.Role
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class PairingExecutionTest : PairingTestBase() {
    private val fake = FakePairingCore()
    override val core: PairingCore = fake

    @Test
    fun execution_closes_handle_on_exception() {
        val started = fake.start(Role.PHONE, SIX_ZEROES, ByteArray(0))
        val execution = PairingExecution(core, started.handle)
        assertEquals(1, fake.openHandles())

        assertThrows(IllegalStateException::class.java) {
            execution.use { throw IllegalStateException("enrollment failed") }
        }

        assertEquals("the handle must be closed on the exception path", 0, fake.openHandles())
        assertEquals(1, fake.closeCalls)

        execution.close()
        assertEquals("close is idempotent", 1, fake.closeCalls)
    }

    @Test
    fun closed_execution_rejects_further_calls() {
        val started = fake.start(Role.PHONE, SIX_ZEROES, ByteArray(0))
        val execution = PairingExecution(core, started.handle)
        execution.close()

        assertThrows(IllegalStateException::class.java) { execution.exchange(ByteArray(POINT_BYTES)) }
        assertThrows(IllegalStateException::class.java) { execution.confirm(ByteArray(CONFIRMATION_BYTES)) }
        assertThrows(IllegalStateException::class.java) { execution.finish() }
    }

    private class FakePairingCore : PairingCore {
        private val handles = HashSet<Long>()
        var closeCalls = 0
            private set

        override fun start(role: Role, code: ByteArray, context: ByteArray): Started {
            val handle = Handle(handles.size.toLong() + 1L)
            handles.add(handle.bits)
            return Started(handle, ByteArray(POINT_BYTES))
        }

        override fun exchange(handle: Handle, peerPoint: ByteArray): ByteArray = ByteArray(CONFIRMATION_BYTES)

        override fun confirm(handle: Handle, peerConfirmation: ByteArray): ByteArray = ByteArray(CONFIRMATION_BYTES)

        override fun finish(handle: Handle): ByteArray {
            handles.remove(handle.bits)
            return ByteArray(KEY_BYTES)
        }

        override fun close(handle: Handle) {
            closeCalls++
            handles.remove(handle.bits)
        }

        override fun openHandles(): Int = handles.size
    }

    private companion object {
        const val POINT_BYTES = 65
        const val CONFIRMATION_BYTES = 32
        const val KEY_BYTES = 16
        val SIX_ZEROES = byteArrayOf(0x30, 0x30, 0x30, 0x30, 0x30, 0x30)
    }
}
