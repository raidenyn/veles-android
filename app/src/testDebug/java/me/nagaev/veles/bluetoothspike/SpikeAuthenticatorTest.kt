package me.nagaev.veles.bluetoothspike

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class SpikeAuthenticatorTest {
    private val clientNonce = "AAECAwQFBgcICQoLDA0ODw"
    private val serverNonce = "EBESExQVFhcYGRobHB0eHw"
    private val sessionId = "ICEiIyQlJicoKSorLC0uLw"

    @Test
    fun `server proof matches shared cross-language vector`() {
        assertEquals(
            "x8jA8SVauz7qJV7-jmtss7zTKkJz0lrMN3kRCVl3pdw",
            SpikeAuthenticator.proof(SpikeProofRole.SERVER, clientNonce, serverNonce, sessionId),
        )
    }

    @Test
    fun `client proof matches shared cross-language vector`() {
        assertEquals(
            "8yMAdRW6GfNygOivmLy1c498vXbXzbN9oMuEfwi0FMI",
            SpikeAuthenticator.proof(SpikeProofRole.CLIENT, clientNonce, serverNonce, sessionId),
        )
    }

    @Test
    fun `verify rejects changed proof`() {
        assertTrue(SpikeAuthenticator.verify(SpikeProofRole.CLIENT, clientNonce, serverNonce, sessionId, "8yMAdRW6GfNygOivmLy1c498vXbXzbN9oMuEfwi0FMI"))
        assertFalse(SpikeAuthenticator.verify(SpikeProofRole.CLIENT, clientNonce, serverNonce, sessionId, "8yMAdRW6GfNygOivmLy1c498vXbXzbN9oMuEfwi0FMJ"))
    }
}
