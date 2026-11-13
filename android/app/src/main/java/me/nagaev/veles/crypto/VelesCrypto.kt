package me.nagaev.veles.crypto

internal object VelesCrypto {
    init {
        System.loadLibrary("veles_crypto")
    }

    @JvmStatic
    external fun reverseBytes(input: ByteArray): ByteArray

    /** Version-1 pairing command API; see protocol/docs/otp-03-api.md. */
    @JvmStatic
    external fun pairingCommand(input: ByteArray): ByteArray
}
