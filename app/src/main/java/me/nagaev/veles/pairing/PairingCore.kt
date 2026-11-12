package me.nagaev.veles.pairing

import me.nagaev.veles.crypto.VelesCrypto
import me.nagaev.veles.session.Role

/**
 * One SPAKE2 execution handle. The core allocates nonzero, monotonically increasing
 * u64 handles; this wrapper keeps the raw bits as a signed 64-bit value so the
 * big-endian JNI encoding round-trips every bit pattern unchanged.
 */
@JvmInline
value class Handle(val bits: Long) {
    init {
        require(bits != 0L) { "pairing handle must be nonzero" }
    }
}

/** The result of [PairingCore.start]: the execution handle and this party's public point. */
data class Started(val handle: Handle, val point: ByteArray)

/** A safe, generic pairing-core failure. It never carries code, key or point bytes. */
class PairingCoreException(message: String) : IllegalStateException(message)

/**
 * The narrow OTP-03 pairing boundary (`docs/protocol/otp-03-api.md`). Tests build
 * consumers against this interface; [VelesPairingCore] is the only production
 * implementation, and it is the only pairing code that touches `VelesCrypto`.
 */
interface PairingCore {
    fun start(role: Role, code: ByteArray, context: ByteArray): Started

    fun exchange(handle: Handle, peerPoint: ByteArray): ByteArray

    fun confirm(handle: Handle, peerConfirmation: ByteArray): ByteArray

    fun finish(handle: Handle): ByteArray

    fun close(handle: Handle)

    fun openHandles(): Int
}

/**
 * JNI implementation of [PairingCore].
 *
 * `VelesCrypto` is resolved lazily: constructing this class, counting handles or
 * closing an unknown handle never loads the native library. That keeps Robolectric
 * tests (whose sandbox class loader must never bind a JNI library) safe when an
 * Android component constructs the seam but no window opens or `pairing_request`
 * arrives.
 *
 * The core has no handle count, so this wrapper counts the handles it allocated.
 * A rejected command destroys the handle inside the core (OTP-03), and `finish` or
 * `close` always consume it, so the count follows those rules instead of relying on
 * caller discipline.
 */
class VelesPairingCore : PairingCore {
    private val lock = Any()
    private val handles = HashSet<Long>()
    private val crypto: VelesCrypto by lazy(LazyThreadSafetyMode.SYNCHRONIZED) { VelesCrypto }

    override fun start(role: Role, code: ByteArray, context: ByteArray): Started {
        require(code.size == CODE_BYTES) { "pairing code must be six bytes" }
        val bodyOffset = COMMAND_PREFIX + ROLE_BYTES
        val command = ByteArray(bodyOffset + CODE_BYTES + context.size)
        command[0] = VERSION.toByte()
        command[1] = OP_START.toByte()
        command[2] = role.wire.toByte()
        code.copyInto(command, bodyOffset)
        context.copyInto(command, bodyOffset + CODE_BYTES)

        val payload = try {
            unwrap(OPERATION_START, invoke(command))
        } finally {
            command.fill(0)
        }
        if (payload.size != HANDLE_BYTES + POINT_BYTES) {
            throw PairingCoreException("pairing core start returned ${payload.size} bytes")
        }
        val handle = Handle(readHandle(payload))
        synchronized(lock) { handles.add(handle.bits) }
        return Started(handle, payload.copyOfRange(HANDLE_BYTES, payload.size))
    }

    override fun exchange(handle: Handle, peerPoint: ByteArray): ByteArray {
        val payload = operation(OPERATION_EXCHANGE, OP_EXCHANGE, handle, peerPoint)
        if (payload.size != EMPTY && payload.size != CONFIRMATION_BYTES) {
            throw PairingCoreException("pairing core exchange returned ${payload.size} bytes")
        }
        return payload
    }

    override fun confirm(handle: Handle, peerConfirmation: ByteArray): ByteArray {
        val payload = operation(OPERATION_CONFIRM, OP_CONFIRM, handle, peerConfirmation)
        if (payload.size != EMPTY && payload.size != CONFIRMATION_BYTES) {
            throw PairingCoreException("pairing core confirm returned ${payload.size} bytes")
        }
        return payload
    }

    override fun finish(handle: Handle): ByteArray {
        try {
            val payload = operation(OPERATION_FINISH, OP_FINISH, handle, EMPTY_BODY)
            if (payload.size != KEY_BYTES) {
                throw PairingCoreException("pairing core finish returned ${payload.size} bytes")
            }
            return payload
        } finally {
            forget(handle)
        }
    }

    override fun close(handle: Handle) {
        forget(handle)
        val command = command(OP_CLOSE, handle, EMPTY_BODY)
        try {
            unwrap(OPERATION_CLOSE, invoke(command))
        } catch (_: PairingCoreException) {
            // Cleanup: the handle is gone either way and a stale close must not mask
            // the caller's real outcome (mirrors the WASM wrapper's discipline).
        } finally {
            command.fill(0)
        }
    }

    override fun openHandles(): Int = synchronized(lock) { handles.size }

    private fun operation(name: String, opcode: Int, handle: Handle, body: ByteArray): ByteArray {
        val command = command(opcode, handle, body)
        return try {
            unwrap(name, invoke(command))
        } catch (failure: PairingCoreException) {
            // A failed operation on an identifiable handle destroys it inside the core.
            forget(handle)
            throw failure
        } finally {
            command.fill(0)
        }
    }

    private fun command(opcode: Int, handle: Handle, body: ByteArray): ByteArray {
        val command = ByteArray(COMMAND_PREFIX + HANDLE_BYTES + body.size)
        command[0] = VERSION.toByte()
        command[1] = opcode.toByte()
        writeHandle(handle, command)
        body.copyInto(command, COMMAND_PREFIX + HANDLE_BYTES)
        return command
    }

    private fun unwrap(operation: String, response: ByteArray): ByteArray {
        val status = response.firstOrNull()?.toInt()?.and(U8_MASK)
            ?: throw PairingCoreException("pairing core $operation returned no status")
        if (status == STATUS_OK) return response.copyOfRange(1, response.size)
        throw PairingCoreException(failureMessage(operation, status))
    }

    private fun failureMessage(operation: String, status: Int): String = when (status) {
        STATUS_GENERIC -> "pairing core $operation failed"
        STATUS_ENTROPY -> "pairing core $operation failed: entropy unavailable"
        STATUS_CAPACITY -> "pairing core $operation failed: handle capacity reached"
        else -> "pairing core $operation failed with status $status"
    }

    private fun invoke(command: ByteArray): ByteArray = crypto.pairingCommand(command)

    private fun forget(handle: Handle) {
        synchronized(lock) { handles.remove(handle.bits) }
    }

    private fun writeHandle(handle: Handle, command: ByteArray) {
        var bits = handle.bits
        for (index in HANDLE_BYTES - 1 downTo 0) {
            command[COMMAND_PREFIX + index] = bits.toByte()
            bits = bits ushr BITS_PER_BYTE
        }
    }

    private fun readHandle(payload: ByteArray): Long {
        var bits = 0L
        for (index in 0 until HANDLE_BYTES) {
            bits = (bits shl BITS_PER_BYTE) or (payload[index].toLong() and BYTE_MASK)
        }
        return bits
    }

    private companion object {
        const val VERSION = 1
        const val OP_START = 1
        const val OP_EXCHANGE = 2
        const val OP_CONFIRM = 3
        const val OP_FINISH = 4
        const val OP_CLOSE = 5
        const val STATUS_OK = 0
        const val STATUS_GENERIC = 1
        const val STATUS_ENTROPY = 2
        const val STATUS_CAPACITY = 4
        const val COMMAND_PREFIX = 2
        const val ROLE_BYTES = 1
        const val HANDLE_BYTES = 8
        const val POINT_BYTES = 65
        const val CONFIRMATION_BYTES = 32
        const val KEY_BYTES = 16
        const val CODE_BYTES = 6
        const val EMPTY = 0
        const val U8_MASK = 0xFF
        const val BYTE_MASK = 0xFFL
        const val BITS_PER_BYTE = 8
        val EMPTY_BODY = ByteArray(0)
        const val OPERATION_START = "start"
        const val OPERATION_EXCHANGE = "exchange"
        const val OPERATION_CONFIRM = "confirm"
        const val OPERATION_FINISH = "finish"
        const val OPERATION_CLOSE = "close"
    }
}
