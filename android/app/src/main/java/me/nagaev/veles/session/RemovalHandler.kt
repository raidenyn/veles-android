package me.nagaev.veles.session

import me.nagaev.veles.trust.TrustStoreException

/**
 * Deletes one peer's trust record for a handler-driven removal (design D2/D4/D8).
 *
 * An implementation takes the per-peer revocation fence, starts the coordinator's detached
 * finish job — so the fence outlives the handler's return and the current generation's
 * teardown — and then deletes the record. A store failure is raised as
 * [TrustStoreException]; the repository's store-failure callback is what escalates it to the
 * fail-closed action, and the engine's own failure path ends this session.
 *
 * The handler never awaits its own generation: awaiting it would deadlock on the mutex the
 * handler is running under.
 */
interface RemovalOperations {
    suspend fun deleteFenced(peerId: String)
}

/**
 * The mutual-removal handler (design D4, OTP2-REC-013): `removal_request` (0x0205) and
 * `removal_response` (0x0206), empty bodies, FULL class only. The record policy and the codec
 * already enforce both rules, so this handler only owns the trust and reply-then-close order.
 *
 * - Responder: on `removal_request`, delete the record of the authenticated peer first, then
 *   reply `removal_response`. It asks for the reply-then-close outcome, so the response is the
 *   last record in that direction and the engine performs the terminal teardown; the handler
 *   never tears the session down itself.
 * - Requester: the engine delivers `removal_response` only while the session is awaiting one
 *   ([PhoneSessionEngine.setRemovalPending]); an unsolicited response is a protocol violation
 *   that the engine ends before this handler runs, so it never deletes anything. On a delivered
 *   response the handler deletes the local record and asks to close after the empty reply set.
 *
 * The session-scoped instance is bound to the authenticated peer id the engine fixed at the
 * authentication gate; no label or address is ever an input.
 */
class RemovalHandler(private val ops: RemovalOperations) : ApplicationHandler {
    override fun handles(type: Int): Boolean = isRemovalRecord(type)

    override suspend fun handle(
        type: Int,
        body: ByteArray,
        reply: suspend (type: Int, body: ByteArray) -> Unit,
    ): Unit = throw SessionReject()

    override fun forSession(context: SessionContext): ApplicationHandler = SessionRemoval(ops, context.peerId)
}

/** One session's removal handler, bound to the peer the engine authenticated. */
private class SessionRemoval(private val ops: RemovalOperations, private val peerId: String) : ApplicationHandler {
    override fun handles(type: Int): Boolean = isRemovalRecord(type)

    override suspend fun handle(
        type: Int,
        body: ByteArray,
        reply: suspend (type: Int, body: ByteArray) -> Unit,
    ) {
        // Delete first (design D2): a failed delete throws TrustStoreException before any reply,
        // so the peer never sees a response for a record this endpoint still holds.
        ops.deleteFenced(peerId)
        if (type == RecordType.REMOVAL_REQUEST.wire) reply(RecordType.REMOVAL_RESPONSE.wire, ByteArray(0))
    }

    override fun closeAfterReply(type: Int): Boolean = isRemovalRecord(type)
}

internal fun isRemovalRecord(type: Int): Boolean = type == RecordType.REMOVAL_REQUEST.wire || type == RecordType.REMOVAL_RESPONSE.wire

/**
 * The requester's removal-pending guard (design D4, OTP2-REC-013). The owner sets it on one
 * session right after it sealed `removal_request`, and teardown clears it.
 *
 * While pending, only `removal_response` may reach the handler (`app_heartbeat` and `app_error`
 * are handled before the guard) and no application record may be sent. A response without the
 * mark is unsolicited: the engine ends that session without deleting anything.
 */
internal class RemovalGuard {
    @Volatile private var pending = false

    fun setPending(on: Boolean) {
        pending = on
    }

    fun reset() {
        pending = false
    }

    /** True when [type] may reach the removal handler in the current state. */
    fun accepts(type: Int): Boolean = when {
        type == RecordType.REMOVAL_RESPONSE.wire -> pending
        pending -> false
        else -> true
    }

    /** False while a removal response is awaited: every application send fails the session. */
    fun allowsSend(): Boolean = !pending
}
