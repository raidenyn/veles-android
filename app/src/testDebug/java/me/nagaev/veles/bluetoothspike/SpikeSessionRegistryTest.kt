package me.nagaev.veles.bluetoothspike

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

class SpikeSessionRegistryTest {
    @Test
    fun `OTP delivery is gated until subscription and client proof succeed`() {
        var now = 1_000L
        val randomValues = ArrayDeque(listOf(ByteArray(16) { 0x10 }, ByteArray(16) { 0x20 }))
        val sessions = SpikeSessionRegistry(
            clockMillis = { now },
            randomBytes = { randomValues.removeFirst() },
        )
        sessions.onConnected("desktop-a", "Windows")
        sessions.onSubscribed("desktop-a")

        val challenge = sessions.beginAuthentication("desktop-a", "AAECAwQFBgcICQoLDA0ODw")
        val proof = SpikeAuthenticator.proof(
            SpikeProofRole.CLIENT,
            challenge.clientNonce!!,
            challenge.serverNonce!!,
            challenge.sessionId!!,
        )

        assertTrue(sessions.authenticate("desktop-a", proof))
        assertEquals(listOf("desktop-a"), sessions.authenticatedTargets())
        assertFalse(sessions.authenticate("desktop-a", proof), "A consumed challenge must not replay")
    }

    @Test
    fun `heartbeat expiry removes only the stale authenticated client`() {
        var now = 1_000L
        val randomValues = ArrayDeque(
            listOf(
                ByteArray(16) { 0x10 },
                ByteArray(16) { 0x20 },
                ByteArray(16) { 0x30 },
                ByteArray(16) { 0x40 },
            ),
        )
        val sessions = SpikeSessionRegistry(
            clockMillis = { now },
            randomBytes = { randomValues.removeFirst() },
        )
        listOf("desktop-a", "desktop-b").forEach { clientId ->
            sessions.onConnected(clientId, clientId)
            sessions.onSubscribed(clientId)
            val challenge = sessions.beginAuthentication(clientId, "AAECAwQFBgcICQoLDA0ODw")
            val proof = SpikeAuthenticator.proof(
                SpikeProofRole.CLIENT,
                challenge.clientNonce!!,
                challenge.serverNonce!!,
                challenge.sessionId!!,
            )
            assertTrue(sessions.authenticate(clientId, proof))
        }

        now += SpikeSessionRegistry.HEARTBEAT_TIMEOUT_MILLIS - 1
        sessions.heartbeat("desktop-b")
        now += 2

        assertEquals(listOf("desktop-a"), sessions.expiredAuthenticatedClients())
        assertEquals(listOf("desktop-b"), sessions.authenticatedTargets())
    }

    @Test
    fun `beginAuthentication requires connected and subscribed client`() {
        var now = 1_000L
        val randomValues = ArrayDeque(listOf(ByteArray(16) { 0x10 }, ByteArray(16) { 0x20 }))
        val sessions = SpikeSessionRegistry(
            clockMillis = { now },
            randomBytes = { randomValues.removeFirst() },
        )

        // Unknown client cannot begin authentication.
        assertFailsWith<IllegalStateException> { sessions.beginAuthentication("ghost", "AAECAwQFBgcICQoLDA0ODw") }

        // Connected but not subscribed cannot begin authentication.
        sessions.onConnected("desktop-a", "Windows")
        assertFailsWith<IllegalStateException> { sessions.beginAuthentication("desktop-a", "AAECAwQFBgcICQoLDA0ODw") }

        // After subscription a challenge is produced with all fields populated.
        sessions.onSubscribed("desktop-a")
        val challenge = sessions.beginAuthentication("desktop-a", "AAECAwQFBgcICQoLDA0ODw")
        assertEquals("AAECAwQFBgcICQoLDA0ODw", challenge.clientNonce)
        assertNotNull(challenge.serverNonce)
        assertNotNull(challenge.sessionId)
        assertNotNull(challenge.proof)
        assertEquals(SpikeProtocol.TYPE_CHALLENGE, challenge.type)
    }

    @Test
    fun `beginAuthentication rejects subscribed-but-not-connected client`() {
        var now = 1_000L
        val randomValues = ArrayDeque(listOf(ByteArray(16) { 0x10 }, ByteArray(16) { 0x20 }))
        val sessions = SpikeSessionRegistry(
            clockMillis = { now },
            randomBytes = { randomValues.removeFirst() },
        )

        // Only subscribing (skipping onConnected) must not auto-create a session,
        // so beginAuthentication must reject the client.
        sessions.onSubscribed("desktop-a")
        assertFailsWith<IllegalStateException> { sessions.beginAuthentication("desktop-a", "AAECAwQFBgcICQoLDA0ODw") }
    }

    @Test
    fun `beginAuthentication rejects client nonce that is not sixteen bytes`() {
        var now = 1_000L
        val randomValues = ArrayDeque(listOf(ByteArray(16) { 0x10 }, ByteArray(16) { 0x20 }))
        val sessions = SpikeSessionRegistry(
            clockMillis = { now },
            randomBytes = { randomValues.removeFirst() },
        )
        sessions.onConnected("desktop-a", "Windows")
        sessions.onSubscribed("desktop-a")

        // Eight bytes instead of sixteen.
        assertFailsWith<IllegalArgumentException> { sessions.beginAuthentication("desktop-a", "AAECAwQFBgc") }
        // Not valid Base64URL at all.
        assertFailsWith<IllegalArgumentException> { sessions.beginAuthentication("desktop-a", "not!base64") }
    }

    @Test
    fun `authenticate fails before any challenge is issued`() {
        val sessions = SpikeSessionRegistry(clockMillis = { 1_000L }, randomBytes = { ByteArray(16) })
        sessions.onConnected("desktop-a", "Windows")
        sessions.onSubscribed("desktop-a")

        assertFalse(sessions.authenticate("desktop-a", "any-proof"))
        assertFalse(sessions.isAuthenticated("desktop-a"))
        assertEquals(emptyList(), sessions.authenticatedTargets())
    }

    @Test
    fun `authenticate fails when the client proof is wrong`() {
        var now = 1_000L
        val randomValues = ArrayDeque(listOf(ByteArray(16) { 0x10 }, ByteArray(16) { 0x20 }))
        val sessions = SpikeSessionRegistry(
            clockMillis = { now },
            randomBytes = { randomValues.removeFirst() },
        )
        sessions.onConnected("desktop-a", "Windows")
        sessions.onSubscribed("desktop-a")
        sessions.beginAuthentication("desktop-a", "AAECAwQFBgcICQoLDA0ODw")

        assertFalse(sessions.authenticate("desktop-a", "wrong-proof"))
        assertFalse(sessions.isAuthenticated("desktop-a"))
    }

    @Test
    fun `authenticate fails when the challenge is older than ten seconds`() {
        var now = 1_000L
        val randomValues = ArrayDeque(listOf(ByteArray(16) { 0x10 }, ByteArray(16) { 0x20 }))
        val sessions = SpikeSessionRegistry(
            clockMillis = { now },
            randomBytes = { randomValues.removeFirst() },
        )
        sessions.onConnected("desktop-a", "Windows")
        sessions.onSubscribed("desktop-a")
        val challenge = sessions.beginAuthentication("desktop-a", "AAECAwQFBgcICQoLDA0ODw")
        val proof = SpikeAuthenticator.proof(
            SpikeProofRole.CLIENT,
            challenge.clientNonce!!,
            challenge.serverNonce!!,
            challenge.sessionId!!,
        )

        now += SpikeSessionRegistry.AUTHENTICATION_TIMEOUT_MILLIS + 1
        assertFalse(sessions.authenticate("desktop-a", proof))
        assertFalse(sessions.isAuthenticated("desktop-a"))
    }

    @Test
    fun `heartbeat updates last activity only for authenticated clients`() {
        var now = 1_000L
        val randomValues = ArrayDeque(listOf(ByteArray(16) { 0x10 }, ByteArray(16) { 0x20 }))
        val sessions = SpikeSessionRegistry(
            clockMillis = { now },
            randomBytes = { randomValues.removeFirst() },
        )
        sessions.onConnected("desktop-a", "Windows")
        sessions.onSubscribed("desktop-a")

        // Heartbeat before authentication is ignored.
        assertFalse(sessions.heartbeat("desktop-a"))

        val challenge = sessions.beginAuthentication("desktop-a", "AAECAwQFBgcICQoLDA0ODw")
        val proof = SpikeAuthenticator.proof(
            SpikeProofRole.CLIENT,
            challenge.clientNonce!!,
            challenge.serverNonce!!,
            challenge.sessionId!!,
        )
        assertTrue(sessions.authenticate("desktop-a", proof))

        // Heartbeat after authentication refreshes the session.
        assertTrue(sessions.heartbeat("desktop-a"))
    }

    @Test
    fun `authenticatedTargets returns sorted client IDs`() {
        var now = 1_000L
        // Register three clients out of order; each authenticate uses two random batches.
        val random = ArrayDeque(
            listOf(
                ByteArray(16) { 0x01 },
                ByteArray(16) { 0x02 },
                ByteArray(16) { 0x03 },
                ByteArray(16) { 0x04 },
                ByteArray(16) { 0x05 },
                ByteArray(16) { 0x06 },
            ),
        )
        val sessions = SpikeSessionRegistry(clockMillis = { now }, randomBytes = { random.removeFirst() })
        listOf("desktop-c", "desktop-a", "desktop-b").forEach { id ->
            sessions.onConnected(id, id)
            sessions.onSubscribed(id)
            val challenge = sessions.beginAuthentication(id, "AAECAwQFBgcICQoLDA0ODw")
            val proof = SpikeAuthenticator.proof(
                SpikeProofRole.CLIENT,
                challenge.clientNonce!!,
                challenge.serverNonce!!,
                challenge.sessionId!!,
            )
            assertTrue(sessions.authenticate(id, proof))
        }
        assertEquals(listOf("desktop-a", "desktop-b", "desktop-c"), sessions.authenticatedTargets())
    }

    @Test
    fun `remove drops a single client and clear resets everything`() {
        var now = 1_000L
        val random = ArrayDeque(
            listOf(
                ByteArray(16) { 0x10 },
                ByteArray(16) { 0x20 },
                ByteArray(16) { 0x30 },
                ByteArray(16) { 0x40 },
            ),
        )
        val sessions = SpikeSessionRegistry(clockMillis = { now }, randomBytes = { random.removeFirst() })
        listOf("desktop-a", "desktop-b").forEach { id ->
            sessions.onConnected(id, id)
            sessions.onSubscribed(id)
            val challenge = sessions.beginAuthentication(id, "AAECAwQFBgcICQoLDA0ODw")
            val proof = SpikeAuthenticator.proof(
                SpikeProofRole.CLIENT,
                challenge.clientNonce!!,
                challenge.serverNonce!!,
                challenge.sessionId!!,
            )
            assertTrue(sessions.authenticate(id, proof))
        }

        sessions.remove("desktop-a")
        assertEquals(listOf("desktop-b"), sessions.authenticatedTargets())
        assertFalse(sessions.isAuthenticated("desktop-a"))

        sessions.clear()
        assertEquals(emptyList(), sessions.authenticatedTargets())
    }

    @Test
    fun `expiredAuthenticatedClients returns empty when nothing is stale`() {
        var now = 1_000L
        val random = ArrayDeque(listOf(ByteArray(16) { 0x10 }, ByteArray(16) { 0x20 }))
        val sessions = SpikeSessionRegistry(clockMillis = { now }, randomBytes = { random.removeFirst() })
        sessions.onConnected("desktop-a", "Windows")
        sessions.onSubscribed("desktop-a")
        val challenge = sessions.beginAuthentication("desktop-a", "AAECAwQFBgcICQoLDA0ODw")
        val proof = SpikeAuthenticator.proof(
            SpikeProofRole.CLIENT,
            challenge.clientNonce!!,
            challenge.serverNonce!!,
            challenge.sessionId!!,
        )
        assertTrue(sessions.authenticate("desktop-a", proof))

        now += 1_000L
        assertEquals(emptyList(), sessions.expiredAuthenticatedClients())
        assertEquals(listOf("desktop-a"), sessions.authenticatedTargets())
    }
}
