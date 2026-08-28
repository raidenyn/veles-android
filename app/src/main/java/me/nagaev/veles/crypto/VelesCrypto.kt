package me.nagaev.veles.crypto

internal object VelesCrypto {
    init {
        System.loadLibrary("veles_crypto")
    }

    @JvmStatic
    external fun reverseBytes(input: ByteArray): ByteArray
}
