package me.nagaev.veles.session

/** OTP2-REC-020a signature frame payload: `role u8 || signature[64]`. */
data class SignatureFrame(val role: Role, val signature: ByteArray) {
    fun encode(): ByteArray {
        if (signature.size != SIGNATURE_BYTES) reject()
        return byteArrayOf(role.wire.toByte()) + signature
    }

    companion object {
        private const val SIGNATURE_BYTES = 64
        private const val PAYLOAD_BYTES = 65
        private const val U8_MASK = 0xFF

        fun decode(payload: ByteArray, expectedRole: Role): SignatureFrame {
            if (payload.size != PAYLOAD_BYTES) reject()
            val role = Role.fromWire(payload[0].toInt() and U8_MASK) ?: reject()
            if (role != expectedRole) reject()
            return SignatureFrame(role, payload.copyOfRange(1, PAYLOAD_BYTES))
        }

        private fun reject(): Nothing = throw SessionReject()
    }
}
