package me.nagaev.veles.session

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.junit.runners.Parameterized

/** §11.3 phone-role rows: local record state × the peer's confirmed `peer_state`. */
@RunWith(Parameterized::class)
class ClassSelectionTest(
    private val recordState: LocalRecordState,
    private val peerState: Int,
    private val expected: SessionClass,
) {
    @Test
    fun phoneRows() = runTest {
        val clock = FakeClock()
        val phone = TestIdentity.generate()
        val client = TestClient()
        val engine = TestPeers.engine(
            signer = phone.signer,
            lookup = TestPeers.lookup(TestPeers.record(client, recordState)),
            clock = clock,
        )

        val progress = engine.driveToPeerConfirm(client, peerState)
        engine.onInbound(progress.clientConfirmRecord)
        val authenticated = engine.onSendComplete(progress.token, true)
            .single() as SessionEffect.Authenticated

        assertEquals(expected, authenticated.sessionClass)
        assertEquals(expected, engine.sessionClass)
        assertTrue(engine.isOpen)
    }

    companion object {
        @JvmStatic
        @Parameterized.Parameters(name = "record {0}, peer_state {1} -> {2}")
        fun rows(): List<Array<Any>> = listOf(
            arrayOf(LocalRecordState.ACTIVE, 0x01, SessionClass.FULL),
            arrayOf(LocalRecordState.ACTIVE, 0x02, SessionClass.FULL_PENDING_PEER),
            arrayOf(LocalRecordState.PENDING, 0x01, SessionClass.ACTIVATION_RECOVERY),
            arrayOf(LocalRecordState.PENDING, 0x02, SessionClass.ACTIVATION_RECOVERY),
            arrayOf(LocalRecordState.COMMITTED_WAITING_ACK, 0x01, SessionClass.ACTIVATION_RECOVERY),
            arrayOf(LocalRecordState.COMMITTED_WAITING_ACK, 0x02, SessionClass.ACTIVATION_RECOVERY),
        )
    }
}
