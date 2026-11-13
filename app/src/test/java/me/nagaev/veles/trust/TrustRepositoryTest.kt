package me.nagaev.veles.trust

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.security.KeyPairGenerator

class TrustRepositoryTest {
    private val keys = KeyPairGenerator.getInstance("EC").apply { initialize(256) }
    private val local = InstallationIdentity("01".repeat(16), TrustEncoding.publicKey(keys.generateKeyPair().public))
    private val storage = MemoryTrustStorage()
    private var keyPresent = false
    private var elapsed = 10L
    private var boot = "boot-a"
    private var wall = 1000L
    private val identity = object : IdentityKeyStore {
        override fun publicKey(): String? = if (keyPresent) local.publicKey else null
        override fun generate(): String {
            keyPresent = true
            return local.publicKey
        }
        override fun sign(message: ByteArray): ByteArray = byteArrayOf()
    }
    private val proofGatedVerifier = ActivationVerifier { _, _, proof -> proof.contentEquals(byteArrayOf(1)) }
    private fun repository(
        confirmed: Boolean = true,
        verifier: ActivationVerifier = ActivationVerifier { _, _, _ -> confirmed },
    ) = TrustRepository(
        storage,
        identity,
        { elapsed },
        { boot },
        { wall },
        verifier,
    )
    private fun peer() = PendingPeer(
        "02".repeat(16),
        TrustEncoding.publicKey(keys.generateKeyPair().public),
        "Desktop",
        1,
        "03".repeat(32),
    )

    @Test fun `pending peer cannot authorize delivery and requires both confirmations`() = runTest {
        val repo = repository()
        repo.identity()
        val peer = peer()
        repo.addPending(peer)
        assertEquals(1000L, repo.peers().single().stateChangedAtMillis)
        assertTrue(repo.activePeers().isEmpty())
        wall = 2000L
        repo.confirm(peer.installationId, ActivationStep.COMMIT, byteArrayOf(1))
        wall = 3000L
        repo.confirm(peer.installationId, ActivationStep.COMMIT, byteArrayOf(1))
        assertEquals(2000L, repo.peers().single().stateChangedAtMillis)
        assertTrue(repo.activePeers().isEmpty())
        repo.confirm(peer.installationId, ActivationStep.ACK, byteArrayOf(2))
        wall = 4000L
        repo.confirm(peer.installationId, ActivationStep.ACK, byteArrayOf(2))
        assertEquals(3000L, repo.peers().single().stateChangedAtMillis)
        assertEquals(peer.installationId, repo.activePeers().single().installationId)
    }

    @Test fun `invalid confirmation and out of order ack do not activate`() = runTest {
        val repo = repository(false)
        repo.identity()
        val peer = peer()
        repo.addPending(peer)
        assertFails { repo.confirm(peer.installationId, ActivationStep.ACK, byteArrayOf()) }
        assertFails { repo.confirm(peer.installationId, ActivationStep.COMMIT, byteArrayOf()) }
        assertTrue(repo.activePeers().isEmpty())
    }

    @Test fun `phone_duplicate_commit_on_active_is_noop_after_mac`() = runTest {
        // protocol/v1/fixtures/records.json#st-rec-full-pending-peer-commit-duplicate-no-regression
        val repo = repository(verifier = proofGatedVerifier)
        repo.identity()
        val peer = peer()
        repo.addPending(peer)
        repo.confirm(peer.installationId, ActivationStep.COMMIT, byteArrayOf(1))
        repo.confirm(peer.installationId, ActivationStep.ACK, byteArrayOf(1))
        val active = repo.peers().single()
        assertEquals(TrustState.ACTIVE, active.state)

        wall = 9000L
        repo.confirm(peer.installationId, ActivationStep.COMMIT, byteArrayOf(1))

        assertEquals(
            "a duplicate commit never moves or re-stamps the active record",
            active,
            repo.peers().single(),
        )
    }

    @Test fun `wrong_mac_duplicate_commit_still_rejected`() = runTest {
        val repo = repository(verifier = proofGatedVerifier)
        repo.identity()
        val peer = peer()
        repo.addPending(peer)
        repo.confirm(peer.installationId, ActivationStep.COMMIT, byteArrayOf(1))
        repo.confirm(peer.installationId, ActivationStep.ACK, byteArrayOf(1))
        val active = repo.peers().single()

        wall = 9000L
        assertFails { repo.confirm(peer.installationId, ActivationStep.COMMIT, byteArrayOf(2)) }

        assertEquals(active, repo.peers().single())
    }

    @Test fun `key loss never silently creates replacement identity`() = runTest {
        val repo = repository()
        repo.identity()
        repo.addPending(peer())
        keyPresent = false
        assertFails { repo.identity() }
        assertFails { repo.activePeers() }
    }

    @Test fun `duplicate peer is rejected without replacing trust`() = runTest {
        val repo = repository()
        repo.identity()
        val peer = peer()
        repo.addPending(peer)
        assertFails { repo.addPending(peer.copy(label = "Replacement")) }
        assertEquals("Desktop", repo.peers().single().label)
    }

    @Test fun `incomplete trust expires on monotonic deadline and boot change`() = runTest {
        val repo = repository()
        repo.identity()
        repo.addPending(peer())
        elapsed += 86_400_000L
        assertTrue(repo.peers().isEmpty())
        repo.addPending(peer())
        boot = "boot-b"
        assertTrue(repository().peers().isEmpty())
    }

    @Test fun `continuity token is random and stable across process recreation`() = runTest {
        val repo = repository()
        repo.identity()
        repo.addPending(peer())
        val token = repo.peers().single().bootToken
        assertEquals(64, token.length)
        assertNotEquals(boot, token)
        assertEquals(token, repository().peers().single().bootToken)
        boot = "another-boot"
        repo.addPending(peer().copy(installationId = "05".repeat(16)))
        assertNotEquals(token, repo.peers().single().bootToken)
    }

    @Test fun `active trust survives reboot and revocation removes only selected peer`() = runTest {
        val repo = repository()
        repo.identity()
        val first = peer()
        repo.addPending(first)
        repo.confirm(first.installationId, ActivationStep.COMMIT, byteArrayOf(1))
        repo.confirm(first.installationId, ActivationStep.ACK, byteArrayOf(1))
        repo.addPending(peer().copy(installationId = "04".repeat(16)))
        boot = "next-boot"
        elapsed = 0L
        assertEquals(1, repo.activePeers().size)
        repo.revoke("04".repeat(16))
        assertEquals(1, repo.peers().size)
        repo.revoke(first.installationId)
        assertTrue(repo.peers().isEmpty())
    }

    @Test fun `invalid point and oversized label reject without persisting partial record`() = runTest {
        val repo = repository()
        repo.identity()
        assertFails { repo.addPending(peer().copy(publicKey = "04" + "00".repeat(64))) }
        assertFails { repo.addPending(peer().copy(label = "é".repeat(17))) }
        assertTrue(repo.peers().isEmpty())
    }

    @Test fun `markAuthenticated stamps lastSeen for every record that can authenticate`() = runTest {
        val repo = repository()
        repo.identity()
        val pending = peer()
        val committed = peer().copy(installationId = "04".repeat(16))
        val active = peer().copy(installationId = "05".repeat(16))
        repo.addPending(pending)
        repo.addPending(committed)
        repo.confirm(committed.installationId, ActivationStep.COMMIT, byteArrayOf(1))
        repo.addPending(active)
        repo.confirm(active.installationId, ActivationStep.COMMIT, byteArrayOf(1))
        repo.confirm(active.installationId, ActivationStep.ACK, byteArrayOf(1))

        val before = repo.peers().associateBy { it.installationId }
        assertEquals(
            setOf(TrustState.PENDING, TrustState.COMMITTED_WAITING_ACK, TrustState.ACTIVE),
            before.values.map { it.state }.toSet(),
        )
        wall = 9000L
        listOf(pending, committed, active).forEach { repo.markAuthenticated(it.installationId) }

        val after = repo.peers().associateBy { it.installationId }
        listOf(pending, committed, active).forEach { peer ->
            val was = before.getValue(peer.installationId)
            val now = after.getValue(peer.installationId)
            assertEquals("lastSeen for ${peer.installationId}", 9000L, now.lastSeenMillis)
            assertEquals("state is never changed", was.state, now.state)
            assertEquals("stateChangedAtMillis is never changed", was.stateChangedAtMillis, now.stateChangedAtMillis)
            assertEquals("pending creation is never changed", was.createdAtMillis, now.createdAtMillis)
            assertEquals("pending expiry is never changed", was.createdElapsedMillis, now.createdElapsedMillis)
        }
    }

    @Test fun `markAuthenticated never extends a pending record lifetime`() = runTest {
        val repo = repository()
        repo.identity()
        val pending = peer()
        repo.addPending(pending)
        wall = 9000L

        repo.markAuthenticated(pending.installationId)

        elapsed = 10L + 86_400_000L
        assertTrue("the pending deadline still starts at creation", repository().peers().isEmpty())
    }

    @Test fun `markAuthenticated rejects an unknown or deleted id`() = runTest {
        val repo = repository()
        repo.identity()
        val pending = peer()
        repo.addPending(pending)

        assertFails { repo.markAuthenticated("09".repeat(16)) }
        repo.revoke(pending.installationId)
        assertFails { repo.markAuthenticated(pending.installationId) }
    }

    @Test fun `markAuthenticated value survives repository recreation`() = runTest {
        val repo = repository()
        repo.identity()
        val pending = peer()
        repo.addPending(pending)
        wall = 9000L

        repo.markAuthenticated(pending.installationId)

        assertEquals(9000L, repository().peers().single().lastSeenMillis)
    }

    private suspend fun assertFails(block: suspend () -> Unit) {
        try {
            block()
            fail("Expected fail-closed rejection")
        } catch (_: TrustException) { }
    }
}

private class MemoryTrustStorage : TrustStorage {
    private var value: TrustSnapshot? = null
    override suspend fun <T> transaction(block: (TrustSnapshot?) -> Pair<TrustSnapshot, T>): T {
        val (next, result) = block(value)
        value = next
        return result
    }
}
