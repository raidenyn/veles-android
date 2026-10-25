package me.nagaev.veles.trust

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.Signature
import java.security.spec.ECGenParameterSpec

/** The alias is private to this installation; no private-key bytes leave Android Keystore. */
class AndroidIdentityKeyStore : IdentityKeyStore {
    private fun store() = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }

    override fun publicKey(): String? = guarded {
        val store = store()
        if (!store.containsAlias(ALIAS)) null else {
            checkTrust(store.isKeyEntry(ALIAS))
            TrustEncoding.publicKey(store.getCertificate(ALIAS).publicKey)
        }
    }

    override fun generate(): String = guarded {
        checkTrust(!store().containsAlias(ALIAS))
        val generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore")
        generator.initialize(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN or KeyProperties.PURPOSE_VERIFY)
                .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_SHA256)
                .setUserAuthenticationRequired(false)
                .build(),
        )
        TrustEncoding.publicKey(generator.generateKeyPair().public)
    }

    /** JCA DER signature. Protocol framing/low-S P1363 normalization belongs to the session codec. */
    override fun sign(message: ByteArray): ByteArray = guarded {
        val key = store().getKey(ALIAS, null) as? java.security.PrivateKey ?: throw TrustException()
        Signature.getInstance("SHA256withECDSA").run { initSign(key); update(message); sign() }
    }

    private fun <T> guarded(block: () -> T): T = try { block() } catch (_: Exception) { throw TrustException() }

    companion object {
        internal const val ALIAS = "veles.installation.identity.v1"
    }
}
