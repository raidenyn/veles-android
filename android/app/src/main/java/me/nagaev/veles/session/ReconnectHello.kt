package me.nagaev.veles.session

/**
 * OTP2-REC-020 reconnect hello payload (frame `0x06`):
 * `installation_id[16] || ephemeral_pub[65] || nonce[16] || role u8 || version u16be ||
 * caps_len u16be || caps`.
 */
data class ReconnectHello(
    val installationId: ByteArray,
    val ephemeralPublicKey: ByteArray,
    val nonce: ByteArray,
    val role: Role,
    val caps: ByteArray,
) {
    fun encode(): ByteArray {
        if (installationId.size != INSTALLATION_BYTES ||
            ephemeralPublicKey.size != POINT_BYTES ||
            nonce.size != NONCE_BYTES
        ) {
            reject()
        }
        requireCaps(caps, KNOWN_CAPABILITIES)
        P256.requireValidPoint(ephemeralPublicKey)
        val payload = ByteArray(FIXED_BYTES + caps.size)
        installationId.copyInto(payload, 0)
        ephemeralPublicKey.copyInto(payload, INSTALLATION_BYTES)
        nonce.copyInto(payload, NONCE_OFFSET)
        payload[ROLE_OFFSET] = role.wire.toByte()
        payload[VERSION_OFFSET] = (PROTOCOL_VERSION ushr BITS_PER_BYTE).toByte()
        payload[VERSION_OFFSET + 1] = PROTOCOL_VERSION.toByte()
        payload[CAPS_LEN_OFFSET] = (caps.size ushr BITS_PER_BYTE).toByte()
        payload[CAPS_LEN_OFFSET + 1] = caps.size.toByte()
        caps.copyInto(payload, FIXED_BYTES)
        return payload
    }

    companion object {
        /** Capabilities this endpoint offers: only the mandatory `VELES_RECORD_V1`. */
        val LOCAL_CAPS: ByteArray = byteArrayOf(0x01)

        private const val INSTALLATION_BYTES = 16
        private const val POINT_BYTES = 65
        private const val NONCE_BYTES = 16
        private const val NONCE_OFFSET = INSTALLATION_BYTES + POINT_BYTES
        private const val ROLE_OFFSET = NONCE_OFFSET + NONCE_BYTES
        private const val VERSION_OFFSET = ROLE_OFFSET + 1
        private const val CAPS_LEN_OFFSET = VERSION_OFFSET + 2
        private const val FIXED_BYTES = CAPS_LEN_OFFSET + 2
        private const val PROTOCOL_VERSION = 1
        private const val MAX_CAPS = 16
        private const val MANDATORY_CAP = 0x01
        private const val U8_MASK = 0xFF
        private const val BITS_PER_BYTE = 8

        private val KNOWN_CAPABILITIES: ByteArray = byteArrayOf(0x01, 0x02, 0x03)

        fun decode(payload: ByteArray, expectedRole: Role): ReconnectHello = decode(payload, expectedRole, KNOWN_CAPABILITIES)

        internal fun decode(payload: ByteArray, expectedRole: Role, knownCaps: ByteArray): ReconnectHello {
            if (payload.size < FIXED_BYTES) reject()
            val role = Role.fromWire(payload[ROLE_OFFSET].toInt() and U8_MASK) ?: reject()
            if (role != expectedRole) reject()
            if (u16(payload, VERSION_OFFSET) != PROTOCOL_VERSION) reject()
            val capsLength = u16(payload, CAPS_LEN_OFFSET)
            if (payload.size != FIXED_BYTES + capsLength) reject()
            val caps = payload.copyOfRange(FIXED_BYTES, payload.size)
            requireCaps(caps, knownCaps)
            val point = payload.copyOfRange(INSTALLATION_BYTES, INSTALLATION_BYTES + POINT_BYTES)
            P256.requireValidPoint(point)
            return ReconnectHello(
                installationId = payload.copyOfRange(0, INSTALLATION_BYTES),
                ephemeralPublicKey = point,
                nonce = payload.copyOfRange(NONCE_OFFSET, NONCE_OFFSET + NONCE_BYTES),
                role = role,
                caps = caps,
            )
        }

        private fun requireCaps(caps: ByteArray, knownCaps: ByteArray) {
            if (caps.isEmpty() || caps.size > MAX_CAPS) reject()
            if ((caps[0].toInt() and U8_MASK) != MANDATORY_CAP) reject()
            var previous = -1
            for (cap in caps) {
                val value = cap.toInt() and U8_MASK
                if (value <= previous) reject()
                if (knownCaps.none { (it.toInt() and U8_MASK) == value }) reject()
                previous = value
            }
        }

        private fun u16(payload: ByteArray, offset: Int): Int {
            val high = (payload[offset].toInt() and U8_MASK) shl BITS_PER_BYTE
            return high or (payload[offset + 1].toInt() and U8_MASK)
        }

        private fun reject(): Nothing = throw SessionReject()
    }
}
