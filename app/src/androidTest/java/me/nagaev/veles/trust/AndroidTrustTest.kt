package me.nagaev.veles.trust

import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import android.content.Context
import android.security.keystore.KeyInfo
import android.security.keystore.KeyProperties
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.security.KeyFactory
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.Signature

@RunWith(AndroidJUnit4::class)
class AndroidTrustTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()
    private lateinit var database: TrustDatabase
    private val keys = AndroidIdentityKeyStore()
    private fun keystore() = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
    private fun repo() = TrustRepository(
        RoomTrustStorage(database), keys, { 10L }, { "boot" }, { 1000L },
        ActivationVerifier { _, _, _ -> false },
    )

    @Before fun setup() {
        keystore().deleteEntry(AndroidIdentityKeyStore.ALIAS)
        File(context.noBackupFilesDir, TrustDatabase.FILE_NAME).delete()
        database = TrustDatabase.open(context)
    }

    @After fun cleanup() {
        database.close()
        context.deleteDatabase(File(context.noBackupFilesDir, TrustDatabase.FILE_NAME).absolutePath)
        keystore().deleteEntry(AndroidIdentityKeyStore.ALIAS)
    }

    @Test fun privateKeyIsNonExportableAndSigns() = runTest {
        val identity = repo().identity()
        val store = keystore()
        val key = store.getKey(AndroidIdentityKeyStore.ALIAS, null) as java.security.PrivateKey
        assertNull(key.encoded)
        val info = KeyFactory.getInstance("EC", "AndroidKeyStore").getKeySpec(key, KeyInfo::class.java)
        assertEquals(256, info.keySize)
        assertEquals(KeyProperties.PURPOSE_SIGN or KeyProperties.PURPOSE_VERIFY, info.purposes)
        assertArrayEquals(arrayOf(KeyProperties.DIGEST_SHA256), info.digests)
        assertFalse(info.isUserAuthenticationRequired)
        val message = "identity self test".toByteArray()
        assertTrue(Signature.getInstance("SHA256withECDSA").run {
            initVerify(store.getCertificate(AndroidIdentityKeyStore.ALIAS).publicKey)
            update(message)
            verify(keys.sign(message))
        })
        assertEquals(identity, repo().identity())
    }

    @Test fun databaseIsBackupExcludedAndSurvivesReopen() = runTest {
        val identity = repo().identity()
        val pair = KeyPairGenerator.getInstance("EC").apply { initialize(256) }.generateKeyPair()
        repo().addPending(PendingPeer("02".repeat(16), TrustEncoding.publicKey(pair.public), "PC", 1, "03".repeat(32)))
        val path = requireNotNull(database.openHelper.writableDatabase.path)
        assertEquals(context.noBackupFilesDir.canonicalFile, File(path).parentFile?.canonicalFile)
        database.close()
        database = TrustDatabase.open(context)
        assertEquals(identity, repo().identity())
        assertEquals(1, repo().peers().size)
        assertEquals(1000L, repo().peers().single().stateChangedAtMillis)
        assertTrue(repo().activePeers().isEmpty())
    }

    @Test fun corruptPayloadFailsClosedWithSafeError() = runTest {
        repo().identity()
        database.trustDao().put(TrustRow(payload = "secret-corrupted-payload"))
        try {
            repo().peers()
            fail("Corruption must reject")
        } catch (error: TrustException) {
            assertFalse(error.toString().contains("secret-corrupted-payload"))
            assertNull(error.cause)
        }
    }

    @Test fun missingKeyDoesNotRegenerateTrust() = runTest {
        repo().identity()
        keystore().deleteEntry(AndroidIdentityKeyStore.ALIAS)
        try { repo().identity(); fail("Key loss must reject") } catch (_: TrustException) { }
        assertNull(keys.publicKey())
    }

    @Test fun missingDatabaseWithSurvivingKeyDoesNotRecreateIdentity() = runTest {
        repo().identity()
        database.close()
        context.deleteDatabase(File(context.noBackupFilesDir, TrustDatabase.FILE_NAME).absolutePath)
        database = TrustDatabase.open(context)
        try { repo().identity(); fail("Metadata loss must reject") } catch (_: TrustException) { }
    }
}
