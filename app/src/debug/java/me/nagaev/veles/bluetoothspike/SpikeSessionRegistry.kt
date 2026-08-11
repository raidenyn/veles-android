package me.nagaev.veles.bluetoothspike

import java.util.Base64

internal class SpikeSessionRegistry(
    private val clockMillis: () -> Long,
    private val randomBytes: (Int) -> ByteArray,
) {
    companion object {
        const val AUTHENTICATION_TIMEOUT_MILLIS = 10_000L
        const val HEARTBEAT_TIMEOUT_MILLIS = 15_000L
        private val urlEncoder = Base64.getUrlEncoder().withoutPadding()
    }

    private class Session(
        var label: String,
        var connected: Boolean = false,
        var subscribed: Boolean = false,
        var pendingChallenge: SpikeWireMessage? = null,
        var challengeIssuedAt: Long = 0L,
        var authenticated: Boolean = false,
        var lastHeartbeat: Long = 0L,
    )

    private val sessions = mutableMapOf<String, Session>()
    private val urlDecoder = Base64.getUrlDecoder()

    @Synchronized
    fun onConnected(clientId: String, label: String) {
        val session = sessions.getOrPut(clientId) { Session(label = label) }
        session.label = label
        session.connected = true
    }

    @Synchronized
    fun onSubscribed(clientId: String) {
        val session = sessions[clientId] ?: return
        session.subscribed = true
    }

    @Synchronized
    fun beginAuthentication(clientId: String, clientNonce: String): SpikeWireMessage {
        val session = sessions[clientId]
        check(session != null && session.connected && session.subscribed) {
            "Client $clientId must be connected and subscribed before authentication"
        }
        require(isValidClientNonce(clientNonce)) {
            "Client nonce must decode to exactly sixteen bytes"
        }

        val serverNonceBytes = randomBytes(16)
        val sessionIdBytes = randomBytes(16)
        val serverNonce = urlEncoder.encodeToString(serverNonceBytes)
        val sessionId = urlEncoder.encodeToString(sessionIdBytes)
        val serverProof = SpikeAuthenticator.proof(
            SpikeProofRole.SERVER,
            clientNonce,
            serverNonce,
            sessionId,
        )
        val challenge = SpikeWireMessage(
            type = SpikeProtocol.TYPE_CHALLENGE,
            clientNonce = clientNonce,
            serverNonce = serverNonce,
            sessionId = sessionId,
            proof = serverProof,
        )
        session.pendingChallenge = challenge
        session.challengeIssuedAt = clockMillis()
        return challenge
    }

    @Synchronized
    fun authenticate(clientId: String, proof: String): Boolean {
        val session = sessions[clientId] ?: return false
        val challenge = session.pendingChallenge
        if (challenge == null) return false
        // Reject proofs after ten seconds, then clear the pending challenge so it
        // cannot be replayed regardless of the verification outcome.
        val stale = clockMillis() - session.challengeIssuedAt > AUTHENTICATION_TIMEOUT_MILLIS
        session.pendingChallenge = null
        if (stale) return false
        val clientNonce = challenge.clientNonce ?: return false
        val serverNonce = challenge.serverNonce ?: return false
        val sessionId = challenge.sessionId ?: return false
        val ok = SpikeAuthenticator.verify(
            SpikeProofRole.CLIENT,
            clientNonce,
            serverNonce,
            sessionId,
            proof,
        )
        if (ok) {
            session.authenticated = true
            session.lastHeartbeat = clockMillis()
        }
        return ok
    }

    @Synchronized
    fun heartbeat(clientId: String): Boolean {
        val session = sessions[clientId] ?: return false
        if (!session.authenticated) return false
        session.lastHeartbeat = clockMillis()
        return true
    }

    @Synchronized
    fun isAuthenticated(clientId: String): Boolean = sessions[clientId]?.authenticated == true

    @Synchronized
    fun authenticatedTargets(): List<String> = sessions.filter { it.value.authenticated }.keys.sorted()

    @Synchronized
    fun expiredAuthenticatedClients(): List<String> {
        val now = clockMillis()
        val stale = sessions
            .filter { it.value.authenticated && now - it.value.lastHeartbeat > HEARTBEAT_TIMEOUT_MILLIS }
            .keys
            .sorted()
        stale.forEach { sessions.remove(it) }
        return stale
    }

    @Synchronized
    fun remove(clientId: String) {
        sessions.remove(clientId)
    }

    @Synchronized
    fun clear() {
        sessions.clear()
    }

    private fun isValidClientNonce(clientNonce: String): Boolean = try {
        urlDecoder.decode(clientNonce).size == 16
    } catch (e: IllegalArgumentException) {
        false
    }
}
