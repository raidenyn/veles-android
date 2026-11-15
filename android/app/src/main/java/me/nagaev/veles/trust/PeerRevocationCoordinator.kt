package me.nagaev.veles.trust

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import me.nagaev.veles.session.RemovalOperations
import me.nagaev.veles.sharing.PeerSessions
import me.nagaev.veles.sharing.RemovalResult

/**
 * One endpoint's revocation coordinator (design D1/D2/D3/D5/D6/D8). It owns the order of every
 * trust-changing operation and composes the repository with the session layer; it never talks to
 * a transport directly and never selects a peer by a label, native handle or hint.
 *
 * The order is fixed per operation:
 * - local [revoke]: fence, delete, terminate the matching sessions, sweep, release the fence.
 *   Every later stage still runs in a `finally`, so a failed delete, a failing transport
 *   disconnect, a failed sweep or a cancellation never leaves the peer fenced.
 * - handler-driven [deleteFenced] (mutual removal): fence, launch the detached finish, delete.
 *   The finish waits behind the handler's whole step (so the reply is delivered first) and only
 *   its own unconditional `finally` releases the fence, never a synchronous fallback.
 * - [failClosed] (store failure): terminate everything, non-destructively, and report the one
 *   "re-pair required" status. It runs asynchronously on [scope], so a store failure observed
 *   inside a handler step can never deadlock on that generation's mutex.
 * - [reset]: reserve this request's admission hold and close admission, wait for any earlier
 *   reset body, terminate everything, then discard the stored trust and the key, and release only
 *   this request's hold. Overlapping resets therefore keep admission closed until the last one
 *   completed. Only an explicit user action reaches this path.
 */
@Suppress("TooManyFunctions") // One method per trust-changing operation plus the private stages.
class PeerRevocationCoordinator(
    private val trust: InstallationTrustRepository,
    private val sessions: PeerSessions,
    private val scope: CoroutineScope,
    private val onStatus: (TrustStatus) -> Unit = {},
    private val historyCleanup: (String) -> Unit = {},
) : RemovalOperations {
    /**
     * The peers as the repository holds them right now, with a `connected` flag from the live
     * session registry. Nothing is cached between calls (design D5).
     */
    suspend fun peers(): List<PeerSummary> = trust.peers().map { peer ->
        PeerSummary(
            installationId = peer.installationId,
            label = peer.label,
            fingerprint = peer.fingerprint,
            deviceType = peer.deviceType,
            state = peer.state,
            createdAtMillis = peer.createdAtMillis,
            lastSeenMillis = peer.lastSeenMillis,
            connected = sessions.hasOpenSession(peer.installationId),
        )
    }

    /**
     * Revokes one peer locally (design D2/D8). The fence is taken first and released last; the
     * delete, the termination, the sweep and the release are nested `finally` blocks under
     * [NonCancellable], so nothing can skip a later stage.
     *
     * A failed delete is a store failure: the repository reports it at its choke point and the
     * composition root routes that to [failClosed]. The failure is still rethrown to the caller
     * after the matching sessions have been terminated and the fence released.
     */
    suspend fun revoke(peerId: String) {
        trust.beginRevocation(peerId)
        withContext(NonCancellable) {
            try {
                delete(peerId)
                cleanupHistory(peerId)
            } finally {
                try {
                    terminate(peerId)
                } finally {
                    try {
                        sweep(peerId)
                    } finally {
                        trust.endRevocation(peerId)
                    }
                }
            }
        }
    }

    /**
     * Asks the peer to remove this endpoint's record (OTP2-REC-013). The local record is deleted
     * only by the validated response's handler, so the user keeps local [revoke] as a separate
     * choice when the response is lost.
     */
    suspend fun removeMutually(peerId: String): RemovalResult = sessions.requestRemoval(peerId)

    /**
     * The fail-closed action of design D6: terminate every session and every enrollment
     * connection, delete nothing, and report the one "re-pair required" status.
     *
     * The termination is launched on [scope] instead of running inline because the callback that
     * reaches this method can run inside a generation's handler step; awaiting that generation
     * from inside its own step would deadlock on the mutex the step holds.
     */
    fun failClosed() {
        onStatus(TrustStatus.RE_PAIR_REQUIRED)
        scope.launch {
            try {
                sessions.closeAll()
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (_: Exception) {
                // Logical termination is authoritative; a failing transport close is best-effort.
            }
        }
    }

    /**
     * The explicit identity reset (design D6): admission is closed before anything is read, every
     * session ends before anything is deleted, then the repository deletes the stored trust
     * without decoding it and the key alias. It is idempotent and is never reached from a
     * failure path.
     *
     * The barrier is released only by the *last* active reset, after its deletions and the
     * identity-cache refresh completed: overlapping requests each hold admission closed while
     * they wait for their serialized body, so an earlier completion can never admit a connection
     * that would miss the still-active reset's snapshot — where that reset could delete trust and
     * refresh the identity without terminating it. Each body publishes its outcome while it still
     * owns the serialized section, so the most recently completed reset decides admission whatever
     * the order in which the requests reserved their holds. A failed reset keeps admission closed,
     * because the store may still hold the identity it could not discard.
     *
     * The hold itself cannot be leaked: taking it is a non-suspending call and every release path
     * is a `finally`, so a cancellation after the request owns admission still gives the hold
     * back. A request cancelled before its body reaches the store publishes no outcome at all.
     *
     * A completed reset is also the one recovery from the latched [TrustStatus.RE_PAIR_REQUIRED]:
     * once the deletions have run and the repository has refreshed the identity cache (its
     * `resetIdentity` does both), nothing is left to re-pair, so `OK` is published. A failing
     * reset throws before this line, so the body publishes [TrustStatus.RE_PAIR_REQUIRED] itself:
     * `resetIdentity` deliberately opens no store-failure episode to do it.
     */
    suspend fun reset() {
        // `null` until this request's body starts discarding the store: a request that never got
        // that far publishes no outcome, so it cannot latch a refusal for a store it never touched.
        var completed: Boolean? = null
        var published = false
        try {
            // Taking the hold cannot be cancelled: `beginReset` is a plain call that closes
            // admission and returns, and the `finally` below always releases it, so a cancellation
            // after this line can never leak the hold and keep admission closed forever.
            sessions.beginReset()
            // Bodies are serialized one at a time, like the Chrome connector's reset chain, so a
            // queued request never tears the store down under a running one while it still holds
            // its own admission hold.
            resetLock.withLock {
                try {
                    try {
                        sessions.closeAll()
                    } catch (cancelled: CancellationException) {
                        throw cancelled
                    } catch (_: Exception) {
                        // The generations were logically torn down; the deletion must still run.
                    }
                    // From here the body discards the store, so a failure or a cancellation is
                    // published fail-closed.
                    completed = false
                    trust.resetIdentity()
                    completed = true
                    onStatus(TrustStatus.OK)
                } finally {
                    // Publish this body's outcome while the serialized section is still held, so
                    // the last body to run is the last to publish, whatever the scheduling of the
                    // reservations: admission always reflects the most recently completed reset.
                    // A body that reached the store and failed reports the fail-closed status
                    // itself; a completed one already published OK above.
                    if (completed == false) onStatus(TrustStatus.RE_PAIR_REQUIRED)
                    published = true
                    sessions.endReset(completed)
                }
            }
        } finally {
            // A request cancelled before its body reached the store releases its hold without an
            // outcome, so the most recently completed body still decides.
            if (!published) sessions.endReset(completed)
        }
    }

    /**
     * The handler-driven removal (design D2/D4/D8): take the fence, start the detached finish and
     * delete inside the handler's step. The finish is started with [CoroutineStart.ATOMIC] so its
     * body begins executing even when [scope] is already cancelled; under [NonCancellable] it
     * runs to completion, and its own unconditional `finally` is the only place the fence is
     * released, so the fence can be neither released early nor released twice.
     */
    override suspend fun deleteFenced(peerId: String) {
        trust.beginRevocation(peerId)
        scope.launch(start = CoroutineStart.ATOMIC) { finishRevocation(peerId) }
        delete(peerId)
        cleanupHistory(peerId)
    }

    /**
     * The detached completion of a handler-driven removal: terminate every generation bound to
     * the peer (including the current one, which is already ending through the engine's terminal
     * path, so this only waits behind its step), run the sweep that removes anything written
     * between the fence and the termination, and release the fence.
     */
    private suspend fun finishRevocation(peerId: String) = withContext(NonCancellable) {
        try {
            terminate(peerId)
        } finally {
            try {
                sweep(peerId)
            } finally {
                trust.endRevocation(peerId)
            }
        }
    }

    /**
     * Serializes reset bodies. Admission is reserved per request in [reset] before it queues, so a
     * queued reset keeps its own hold while it waits.
     */
    private val resetLock = Mutex()

    /** Delete first, always terminate (design D2). */
    private suspend fun delete(peerId: String) {
        trust.revoke(peerId)
    }

    /**
     * The final sweep of design D8: delete the id again so an `addPending` that passed the fence
     * before it existed cannot outlive the revocation. The repository reports a store failure at
     * its choke point and rethrows it; that callback is the escalation, so the sweep itself does
     * not report twice and never aborts the fence release.
     */
    private suspend fun sweep(peerId: String) {
        try {
            trust.revoke(peerId)
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (_: TrustException) {
            // Reported by the repository's store-failure episode; the release still runs.
        }
    }

    /** Terminate every matching generation; a failing transport close is best-effort. */
    private suspend fun terminate(peerId: String) {
        try {
            sessions.closePeer(peerId)
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (_: Exception) {
            // The engine teardown already erased the keys and stopped record processing.
        }
    }

    /**
     * The OTP-history extension point: it has no consumer yet and never fails a removal. A
     * consumer runs inside the removal's already-committed delete, so everything it throws —
     * including a cancellation of its own synchronous work — is isolated here; only cancellation
     * of the coordinator's own suspension points propagates.
     */
    private fun cleanupHistory(peerId: String) {
        try {
            historyCleanup(peerId)
        } catch (_: Exception) {
            // OTP-13 registers the first consumer; nothing it throws may fail a removal whose
            // record is already deleted.
        }
    }
}
