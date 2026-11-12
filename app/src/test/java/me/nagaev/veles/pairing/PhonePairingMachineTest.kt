package me.nagaev.veles.pairing

import kotlinx.coroutines.runBlocking
import me.nagaev.veles.session.P256
import me.nagaev.veles.session.Role
import me.nagaev.veles.session.TestPeers
import me.nagaev.veles.trust.ActivationStep
import me.nagaev.veles.trust.InstallationIdentity
import me.nagaev.veles.trust.InstallationTrustRepository
import me.nagaev.veles.trust.PendingPeer
import me.nagaev.veles.trust.TrustedPeer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.security.interfaces.ECPublicKey

/**
 * §11.1 phone pairing machine through a verified `cB`: admission order
 * (framing/`pairing_id` → deadline → point validity → atomic charge → deadline →
 * `exchange`), the generic `preauth_reject` on every failure, the 30 s inactivity
 * timeout and the single handle per execution.
 */
class PhonePairingMachineTest : PairingTestBase() {
    private val fake = FakeCore()
    override val core: PairingCore = fake

    @Test
    fun sixth_attempt_gets_preauth_reject() = runBlocking {
        val harness = harness(PairingLimits(attemptsPerPairingId = 5, rollingAttemptsMax = 10))
        try {
            harness.open()
            repeat(5) { assertEquals(Decision.Allow, harness.admission.chargeAttempt(harness.window.pairingId)) }

            val machine = harness.newMachine()
            harness.offer(machine)
            val refusal = harness.send(machine, pointFrame(validPoint()))

            assertEquals("a sixth attempt is refused with the generic frame", PREAUTH_REJECT_WIRE, wire(refusal))
            assertEquals("nothing is exchanged past the budget", 0, fake.count("exchange"))
            assertEquals(PairingState.WINDOW_OPEN, machine.state)
            assertEquals("a refused attempt is not charged", 5, harness.admission.attemptsChargedForTest(harness.window.pairingId))
        } finally {
            harness.closeAll()
        }
    }

    @Test
    fun invalid_point_is_not_charged() = runBlocking {
        val harness = harness(PairingLimits(attemptsPerPairingId = 1))
        try {
            harness.open()
            val first = harness.newMachine()
            harness.offer(first)

            val refusal = harness.send(first, pointFrame(INVALID_POINT))

            assertEquals("an invalid point is the generic failure", PREAUTH_REJECT_WIRE, wire(refusal))
            assertEquals("no password-sensitive operation runs", 0, fake.count("exchange"))
            assertEquals("a bad point is never charged", 0, harness.admission.attemptsChargedForTest(harness.window.pairingId))
            assertEquals("the execution is erased", 0, fake.openHandles())

            // The surviving budget still allows one charge on a fresh connection.
            val second = harness.newMachine()
            harness.offer(second)
            harness.send(second, pointFrame(validPoint()))
            assertEquals(1, harness.admission.attemptsChargedForTest(harness.window.pairingId))
        } finally {
            harness.closeAll()
        }
    }

    @Test
    fun valid_pb_charged_before_exchange_even_on_failure() = runBlocking {
        val harness = harness(PairingLimits(attemptsPerPairingId = 1))
        var chargedAtExchange = -1
        fake.onExchange = { chargedAtExchange = harness.admission.attemptsChargedForTest(harness.window.pairingId) }
        fake.exchangeFails = true
        try {
            harness.open()
            val first = harness.newMachine()
            harness.offer(first)

            val refusal = harness.send(first, pointFrame(validPoint()))

            assertEquals("the core failure is generic", PREAUTH_REJECT_WIRE, wire(refusal))
            assertEquals("the attempt is consumed before the exchange", 1, chargedAtExchange)
            assertEquals("a failed exchange never refunds", 1, harness.admission.attemptsChargedForTest(harness.window.pairingId))

            // The one-attempt budget is gone, even though the execution never produced cA.
            val second = harness.newMachine()
            harness.offer(second)
            assertEquals(PREAUTH_REJECT_WIRE, wire(harness.send(second, pointFrame(validPoint()))))
        } finally {
            fake.onExchange = null
            fake.exchangeFails = false
            harness.closeAll()
        }
    }

    @Test
    fun deadline_299999_ok_300000_rejected() = runBlocking {
        val open = harness(PairingLimits(), initial = 0L)
        try {
            open.open()
            open.now = 1_000L
            val machine = open.newMachine()
            open.offer(machine)
            open.now = 299_999L

            val accepted = open.send(machine, pointFrame(validPoint()))

            assertEquals("299 999 ms is inside the window", CA_TYPE, frameType(accepted))
            assertEquals(PairingState.AWAITING_CB, machine.state)
        } finally {
            open.closeAll()
        }

        val late = harness(PairingLimits(attemptsPerPairingId = 1), initial = 0L)
        val exchangesBefore = fake.count("exchange")
        try {
            late.open()
            late.now = 1_000L
            val machine = late.newMachine()
            late.offer(machine)
            late.now = 300_000L

            val refusal = late.send(machine, pointFrame(validPoint()))

            assertEquals("300 000 ms is outside the window", PREAUTH_REJECT_WIRE, wire(refusal))
            assertEquals(PairingState.IDLE, machine.state)
            assertFalse("the expired window is closed", late.window.isOpen())
            assertEquals("nothing is computed", exchangesBefore, fake.count("exchange"))
            assertEquals("the expired progression is not charged", 0, late.admission.attemptsChargedForTest(late.pairingIdAtOpen))
        } finally {
            late.closeAll()
        }
    }

    @Test
    fun deadline_rechecked_before_exchange() = runBlocking {
        var now = 0L
        var advance = false
        val admission = PairingAdmission(PairingLimits()) { now.also { if (advance) now += 1L } }
        val window = PairingWindow({ now })
        window.open()
        val pairingId = window.pairingId
        val machine = PhonePairingMachine(window, admission, CONN_ONE, NoTrust, { fake }, { now }, PairingLimits())
        try {
            now = 1_000L
            machine.onFrame(requestFrame())
            now = 299_999L
            advance = true

            val refusal = machine.onFrame(pointFrame(validPoint()))

            assertEquals("the recheck rejects with the same bytes", PREAUTH_REJECT_WIRE, wire(refusal))
            assertEquals("the deadline lands before any computation", 0, fake.count("exchange"))
            assertEquals("the charge happened before the recheck", 1, admission.attemptsChargedForTest(pairingId))
            assertEquals(PairingState.IDLE, machine.state)
        } finally {
            machine.onDisconnect()
        }
    }

    @Test
    fun second_pa_on_connection_rejected_zero_core_calls() = runBlocking {
        val harness = harness()
        try {
            harness.open()
            val first = harness.newMachine(conn = CONN_ONE)
            harness.offer(first)
            assertEquals(1, fake.count("start"))
            first.onDisconnect()

            val second = harness.newMachine(conn = CONN_ONE)
            val refusal = harness.send(second, requestFrame())

            assertEquals("a second fresh pA on one connection is refused", PREAUTH_REJECT_WIRE, wire(refusal))
            assertEquals("no scalar is generated for the refused request", 1, fake.count("start"))
        } finally {
            harness.closeAll()
        }
    }

    @Test
    fun concurrent_second_request_rejected_without_charge() = runBlocking {
        val harness = harness()
        try {
            harness.open()
            val first = harness.newMachine(conn = CONN_ONE)
            harness.offer(first)
            val second = harness.newMachine(conn = CONN_TWO)

            val refusal = harness.send(second, requestFrame())

            assertEquals("one SPAKE2 operation in flight only", PREAUTH_REJECT_WIRE, wire(refusal))
            assertEquals("the second request never starts a core execution", 1, fake.count("start"))
            assertEquals("no attempt is consumed by a request", 0, harness.admission.attemptsChargedForTest(harness.window.pairingId))
            assertEquals("the busy slot is not stolen", 1, harness.admission.operationsInFlightForTest())

            // The holding connection is unaffected and charges its one attempt.
            harness.send(first, pointFrame(validPoint()))
            assertEquals(1, harness.admission.attemptsChargedForTest(harness.window.pairingId))
        } finally {
            harness.closeAll()
        }
    }

    @Test
    fun replayed_pb_on_fresh_connection_charged_again_and_fails() = runBlocking {
        val harness = harness(PairingLimits(attemptsPerPairingId = 2))
        try {
            harness.open()
            val replay = validPoint()
            val first = harness.newMachine(conn = CONN_ONE)
            harness.offer(first)
            harness.send(first, pointFrame(replay))
            first.onDisconnect()

            val second = harness.newMachine(conn = CONN_TWO)
            harness.offer(second)
            harness.send(second, pointFrame(replay))
            assertEquals("a fresh execution, not the old one", 2, fake.count("start"))
            assertEquals(2, fake.count("exchange"))
            assertNotEquals("a new core handle is allocated", fake.handles[0], fake.handles[1])

            // The replayed point cannot confirm against the new execution.
            fake.confirmFails = true
            val refusal = harness.send(second, confirmFrame(ByteArray(CONFIRMATION_BYTES)))
            assertEquals(PREAUTH_REJECT_WIRE, wire(refusal))
            assertEquals("the replayed attempt is charged again", 2, harness.admission.attemptsChargedForTest(harness.window.pairingId))

            val third = harness.newMachine(conn = CONN_THREE)
            harness.offer(third)
            assertEquals("the budget is lower", PREAUTH_REJECT_WIRE, wire(harness.send(third, pointFrame(validPoint()))))
        } finally {
            fake.confirmFails = false
            harness.closeAll()
        }
    }

    @Test
    fun all_failures_identical_bytes() = runBlocking {
        val failures = listOf(
            exhaustedBudgetFailure(),
            badPointFailure(),
            closedWindowFailure(),
            wrongCodeFailure(),
        )

        val first = failures.first()
        assertEquals("every failure is one empty preauth_reject then a close", PREAUTH_REJECT_WIRE, first)
        failures.forEach { assertEquals("all failures are byte-identical", first, it) }
    }

    @Test
    fun inactivity_30s_closes_and_releases_handle() = runBlocking {
        val harness = harness(PairingLimits(attemptsPerPairingId = 1))
        try {
            harness.open()
            val machine = harness.newMachine()
            harness.offer(machine)
            harness.send(machine, pointFrame(validPoint()))
            assertEquals(PairingState.AWAITING_CB, machine.state)
            assertEquals("the execution holds one handle", 1, fake.openHandles())

            harness.now += 29_999L
            assertTrue("29 999 ms of silence is not a timeout", machine.onTick().isEmpty())
            assertEquals(1, fake.openHandles())

            harness.now += 1L
            val timeout = machine.onTick()

            assertEquals("the idle execution is closed", listOf(Outgoing.Disconnect), timeout)
            assertEquals("the handle is released", 0, fake.openHandles())
            assertEquals("the slot is released", 0, harness.admission.operationsInFlightForTest())
            assertEquals("the window stays resumable", PairingState.WINDOW_OPEN, machine.state)
            assertTrue(harness.window.isOpen())
        } finally {
            harness.closeAll()
        }
    }

    @Test
    fun no_window_request_counts_against_the_request_budget() = runBlocking {
        // The `pA` budgets are raised so only the request ceiling can refuse the last one.
        val harness = harness(PairingLimits(freshPaRollingMax = 50, malformedConnectionsRollingMax = 100))
        try {
            val cold = harness.newMachine()
            assertEquals(
                "a request with no open window is refused",
                PREAUTH_REJECT_WIRE,
                wire(harness.send(cold, requestFrame())),
            )
            assertEquals(PairingState.IDLE, cold.state)

            harness.open()
            repeat(9) { index ->
                val machine = harness.newMachine()
                harness.offer(machine)
                machine.onDisconnect()
            }

            val eleventh = harness.newMachine()
            assertEquals(
                "the window-closed request spent the request budget",
                PREAUTH_REJECT_WIRE,
                wire(harness.send(eleventh, requestFrame())),
            )
        } finally {
            harness.closeAll()
        }
    }

    @Test
    fun unexpected_core_failure_still_gets_the_generic_reject() = runBlocking {
        val harness = harness(PairingLimits(attemptsPerPairingId = 1))
        fake.exchangeThrowsUnexpected = true
        try {
            harness.open()
            val machine = harness.newMachine()
            harness.offer(machine)

            val refusal = harness.send(machine, pointFrame(validPoint()))

            assertEquals("an unexpected core failure is the identical generic frame", PREAUTH_REJECT_WIRE, wire(refusal))
            assertEquals("the connection is closed", 0, fake.openHandles())
            assertEquals("the attempt stays consumed", 1, harness.admission.attemptsChargedForTest(harness.window.pairingId))
            assertEquals(PairingState.WINDOW_OPEN, machine.state)
        } finally {
            fake.exchangeThrowsUnexpected = false
            harness.closeAll()
        }
    }

    private suspend fun exhaustedBudgetFailure(): List<String> {
        val harness = harness(PairingLimits(attemptsPerPairingId = 1, rollingAttemptsMax = 10))
        return try {
            harness.open()
            assertEquals(Decision.Allow, harness.admission.chargeAttempt(harness.window.pairingId))
            val machine = harness.newMachine()
            harness.offer(machine)
            wire(harness.send(machine, pointFrame(validPoint())))
        } finally {
            harness.closeAll()
        }
    }

    private suspend fun badPointFailure(): List<String> {
        val harness = harness()
        return try {
            harness.open()
            val machine = harness.newMachine()
            harness.offer(machine)
            wire(harness.send(machine, pointFrame(INVALID_POINT)))
        } finally {
            harness.closeAll()
        }
    }

    private suspend fun closedWindowFailure(): List<String> {
        val harness = harness(initial = 0L)
        return try {
            harness.open()
            harness.now = 1_000L
            val machine = harness.newMachine()
            harness.offer(machine)
            harness.now = 300_000L
            wire(harness.send(machine, pointFrame(validPoint())))
        } finally {
            harness.closeAll()
        }
    }

    private suspend fun wrongCodeFailure(): List<String> {
        val harness = harness()
        fake.confirmFails = true
        return try {
            harness.open()
            val machine = harness.newMachine()
            harness.offer(machine)
            harness.send(machine, pointFrame(validPoint()))
            wire(harness.send(machine, confirmFrame(ByteArray(CONFIRMATION_BYTES))))
        } finally {
            fake.confirmFails = false
            harness.closeAll()
        }
    }

    private fun harness(
        limits: PairingLimits = PairingLimits(),
        initial: Long = 1_000L,
    ): Harness = Harness(fake, limits, initial)

    private fun validPoint(): ByteArray = TestPeers.uncompressed(P256.generate().public as ECPublicKey)

    private class Harness(private val fake: FakeCore, private val limits: PairingLimits, initial: Long) {
        var now = initial
        val window = PairingWindow({ now })
        val admission = PairingAdmission(limits) { now }
        var pairingIdAtOpen: ByteArray = ByteArray(0)
        private var nonce = 0
        private var conn = 1L
        private val machines = mutableListOf<PhonePairingMachine>()

        fun open() {
            window.open()
            pairingIdAtOpen = window.pairingId
        }

        fun newMachine(conn: Long = this.conn++): PhonePairingMachine = PhonePairingMachine(
            window,
            admission,
            conn,
            NoTrust,
            { fake },
            { now },
            limits,
            {
                ByteArray(NONCE_BYTES) {
                    ++nonce
                    nonce.toByte()
                }
            },
        ).also { machines += it }

        /** Sends a request and asserts the offer reached the wire. */
        suspend fun offer(machine: PhonePairingMachine) {
            val outgoing = machine.onFrame(requestFrame())
            assertEquals("the offer is the only frame", OFFER_TYPE, frameType(outgoing))
            assertEquals(PairingState.SPAKE_IN_FLIGHT, machine.state)
        }

        suspend fun send(machine: PhonePairingMachine, frame: ByteArray): List<Outgoing> = machine.onFrame(frame)

        fun closeAll() {
            runBlocking { machines.forEach { it.onDisconnect() } }
            machines.clear()
        }
    }

    private class FakeCore : PairingCore {
        private val open = LinkedHashSet<Long>()
        private var next = 0L
        val calls = mutableListOf<String>()
        val handles = mutableListOf<Long>()
        var confirmFails = false
        var exchangeFails = false
        var exchangeThrowsUnexpected = false
        var onExchange: (() -> Unit)? = null

        override fun start(role: Role, code: ByteArray, context: ByteArray): Started {
            calls += "start"
            val handle = Handle(++next)
            open += handle.bits
            handles += handle.bits
            return Started(handle, FAKE_POINT.copyOf())
        }

        override fun exchange(handle: Handle, peerPoint: ByteArray): ByteArray {
            calls += "exchange"
            onExchange?.invoke()
            if (exchangeThrowsUnexpected) error("unexpected core failure")
            if (exchangeFails) throw PairingCoreException("exchange failed")
            return FAKE_CA.copyOf()
        }

        override fun confirm(handle: Handle, peerConfirmation: ByteArray): ByteArray {
            calls += "confirm"
            if (confirmFails) throw PairingCoreException("confirmation failed")
            return ByteArray(0)
        }

        override fun finish(handle: Handle): ByteArray {
            calls += "finish"
            return ByteArray(16)
        }

        override fun close(handle: Handle) {
            calls += "close"
            open -= handle.bits
        }

        override fun openHandles(): Int = open.size

        fun count(name: String): Int = calls.count { it == name }
    }

    private object NoTrust : InstallationTrustRepository {
        override suspend fun identity(): InstallationIdentity = error("enrollment is not under test")
        override suspend fun peers(): List<TrustedPeer> = emptyList()
        override suspend fun activePeers(): List<TrustedPeer> = emptyList()
        override suspend fun addPending(peer: PendingPeer) = error("enrollment is not under test")
        override suspend fun confirm(peerId: String, step: ActivationStep, proof: ByteArray) = error("enrollment is not under test")
        override suspend fun revoke(peerId: String) = error("enrollment is not under test")
        override suspend fun markAuthenticated(peerId: String) = error("enrollment is not under test")
    }

    private companion object {
        const val NONCE_BYTES = 16
        const val CONFIRMATION_BYTES = 32
        const val CONN_ONE = 1L
        const val CONN_TWO = 2L
        const val CONN_THREE = 3L
        const val VERSION_OFFSET = 16
        const val CAPS_LEN_OFFSET = 18
        const val REQUEST_FIXED_BYTES = 20
        const val OFFER_TYPE = 0x02
        const val CA_TYPE = 0x04
        val PREAUTH_REJECT_WIRE = listOf("0f0000", "close")

        fun wire(outgoing: List<Outgoing>): List<String> = outgoing.map { item ->
            when (item) {
                is Outgoing.Frame -> item.bytes.joinToString("") { (it.toInt() and 0xFF).toString(16).padStart(2, '0') }
                Outgoing.Disconnect -> "close"
            }
        }

        fun frameType(outgoing: List<Outgoing>): Int {
            val frame = outgoing.filterIsInstance<Outgoing.Frame>().single()
            return frame.bytes[0].toInt() and 0xFF
        }

        fun requestFrame(clientNonce: ByteArray = CLIENT_NONCE, caps: ByteArray = CLIENT_CAPS): ByteArray {
            val payload = ByteArray(REQUEST_FIXED_BYTES + caps.size)
            clientNonce.copyInto(payload)
            payload[VERSION_OFFSET] = 0
            payload[VERSION_OFFSET + 1] = 1
            payload[CAPS_LEN_OFFSET] = (caps.size ushr 8).toByte()
            payload[CAPS_LEN_OFFSET + 1] = caps.size.toByte()
            caps.copyInto(payload, REQUEST_FIXED_BYTES)
            return PairingFrames.encode(PairingFrameType.REQUEST, payload)
        }

        fun pointFrame(point: ByteArray): ByteArray = PairingFrames.encode(PairingFrameType.PB, point)

        fun confirmFrame(cb: ByteArray): ByteArray = PairingFrames.encode(PairingFrameType.CB, cb)

        val CLIENT_NONCE = ByteArray(16) { 0x11 }
        val CLIENT_CAPS = byteArrayOf(0x01, 0x03)
        val INVALID_POINT = ByteArray(65)
        val FAKE_POINT = ByteArray(65) { 0x04 }
        val FAKE_CA = ByteArray(32) { 0x0A }
    }
}
