## Context

OTP-04/05 gave both endpoints identities and fail-closed trust stores, OTP-09 authenticated
reconnect, and OTP-10 one-time enrollment and activation. Real OTP delivery (Gate "Blocks real
OTP") needs each side to be able to remove a peer and recover from lost state.

Current state:

- Android: `TrustRepository.revoke(peerId)` deletes a record of any state. `SessionHost` binds a
  session to `Live.peerId` and exposes `hasOpenSession`/`authenticatedSessions`; closing a
  generation is `teardownLocked` plus `transport.close`. Application records reach an
  `ApplicationHandler` (`session/SessionEffect.kt`), composed in `session/HandlerComposition.kt`;
  `ActivationEngine` is the model handler. `AndroidIdentityKeyStore` has no delete.
- Chrome: `ChromeTrustRepository` has `revoke`, `revokePending`, `forgetHint`, `reset`. The
  `platformHint` is a field of the peer record. `SessionManager` keeps `byHandle` with `peerId` and
  closes by handle; the engine routes application records to `deps.handler`.
- Contract: OTP2-REC-013 defines mutual removal. `removal_request` (0x0205) and `removal_response`
  (0x0206) have empty bodies and are FULL-class only. Wire types, policy, codec, state machine
  entries and a conformance fixture exist; no handler exists. A later reconnect of a removed peer
  fails with the same generic authentication failure as any untrusted peer (OTP2-SEC-004); no
  revoked-peer record is kept.
- Re-pairing is refused while the phone holds a record for the installation ID (OTP2-STA-012), so
  revocation precedes it.

## Goals / Non-Goals

**Goals:**

- List peers with fingerprint, label, device type, state, created/last-seen and connected status.
- Revoke one peer locally: delete only its trust (and, on Chrome, its platform hint) and close
  only its sessions. An offline counterpart cannot regain access.
- Mutual removal over OTP2-REC-013, with offline semantics when the response is lost.
- Key and storage loss or corruption fails closed; an explicit reset returns to untrusted; pairing
  again creates explicit new trust.
- Labels, native handles and platform hints never change identity.
- Debug-only controls on both endpoints to exercise these flows.

**Non-Goals:**

- Final Android and Chrome visual design (OTP-16/17).
- Any protocol or fixture change.
- The OTP history buffer and its cleanup (OTP-13); only an extension point is provided.
- The watch PIN reset (no watch endpoint exists yet).
- A revoked-peer tombstone or an authenticated proof of revocation (the spec forbids requiring one).

## Decisions

**D1. One revocation coordinator per endpoint (Kotlin and TypeScript).** It owns ordering and the
mutual-removal exchange and composes the existing repository and session layer. Alternatives
rejected: reactive teardown (a live session can outlive its record; no help for the ordered
exchange) and engine-owned revocation (couples storage into protocol engines).

**D2. Delete first, then disconnect, and always disconnect.** Local revoke deletes the durable
record in one repository transaction, then clears dependent state, then terminates matching
sessions. Session termination runs in a `finally`: if the delete, the hint cleanup or the reset
throws, every matching session (every session, for a reset or a trust-unavailable status) is
still terminated, because trust is unproven. *Logical* termination (engine teardown, which erases
session keys and stops record processing) is authoritative; the transport disconnect is
best-effort and may fail without leaving authorization behind. When the revoke or reset runs
inside a session handler (mutual removal), the *current* generation is never awaited: its
termination is performed by the engine through the terminal path of D4 (on failure, the handler
throws and the engine's existing failure path terminates it), while every other matching
generation is terminated asynchronously on the host scope. If the process dies between steps,
trust is already gone, so the failure mode is fail-closed.

**D3. Terminate by peer, never by address, and include enrollment connections.** Android adds
`SessionHost.closePeer(peerIdHex)`. It selects every generation whose authenticated `Live.peerId`
matches **and** every generation whose pairing connection is bound to that peer (a new
`PairingConnection.boundPeerId()`, set once the peer identity is authenticated, so a pending or
`committed_waiting_ack` record is also covered). Each selected generation runs the existing
`teardownLocked` plus `transport.close` under that generation's own mutex. Connections of other
peers, and enrollments not yet bound to a peer, are untouched; `reset` additionally calls
`cancelPairings()` and closes every generation. Chrome adds `SessionManager.closePeer(peerId)`
that terminates every matching handle. The enrollment link (`pairing/link.ts`), which lives
outside `SessionManager`, gains `boundPeerId()` (set when the peer identity is authenticated) and
`cancel()`, and the connector registers each live link with the coordinator so `closePeer` can
cancel the matching one and fence any asynchronous `addPending` it has underway (D8).
Native handles and hints are never an input to selection.

**D4. Removal handler on each endpoint, with an explicit reply-then-close effect.** An
`ApplicationHandler` handles 0x0205/0x0206 and is composed alongside the activation handler in
production builds (not only in debug). Today a handler can only `reply`, and the engine buffers
replies until the handler returns; a handler that tears the session down itself would discard its
own reply or deadlock on the generation's mutex. So `ApplicationHandler` gains a defaulted
`closeAfterReply(type): Boolean` (Kotlin) and the equivalent in the TypeScript handler. When it
returns true, the engine seals the buffered replies, tears itself down (the existing
terminal-reply path), and emits `Terminated` plus `Close`, so the response is the last record in
that direction and the session accepts nothing further.
- *Responder* (`removal_request` on a FULL session): inside the handler, delete the record
  (delete-first, D2), reply `removal_response`, and return `closeAfterReply = true`. The other
  sessions and enrollment connections of that peer are terminated by the coordinator through
  `closePeer`, posted asynchronously on the host scope and excluding the current generation so it
  never waits on the mutex it is running under.
- *Requester*: the coordinator sends `removal_request` and sets a removal-pending marker on that
  session only. From then on the session accepts only `removal_response`, heartbeat and
  `app_error`; any other application record, and any application send, fails the session (no
  further records flow). A valid `removal_response` deletes the local record and closes. An
  unsolicited `removal_response`, or one without the marker, is a protocol violation. Loss of the
  response, a disconnect or a timeout (injected clock) keeps the record and reports that the
  removal completed locally only if the user chooses local removal.
- `CompositeApplicationHandler` (Kotlin) and the composed handler in TypeScript forward
  `closeAfterReply` to the child that handled the type; a composed-handler test proves a removal
  through the production composition terminates, since a missed forward would silently disable it.
- Both sides call the OTP history cleanup extension point; it has no registered consumer yet.

**D5. Peer listing model.** A read-only `PeerSummary` (id, label, fingerprint, device type, state,
createdAt, lastSeen, connected) is built from the repository plus the session registry. It is
computed fresh on each call; it carries no key material, no hint and no handle.

**D6. Key loss and corruption.** Today one exception type covers both a store-level failure (missing key, key/metadata mismatch,
corrupt or undecodable data) and an expected peer-specific rejection (`confirm` after a
revocation), and repository failures are mostly caught where they occur, ending only that
connection. So the repositories classify at one choke point, by **what failed**: a peer
rejection is only a failure raised by the operation's own explicit peer-level checks (record
missing, wrong state) or by the generation and revocation fences (D8); everything else is a store
failure. That covers failures before the operation runs (storage read and decode, identity read,
owner liveness, snapshot shape and per-peer validation) **and** after it returns (validation of
the resulting snapshot and the durable write).
- Android: `TrustStoreException : TrustException`. `RoomTrustStorage.transaction` throws it for
  decode and database errors (it decodes before invoking the block today), and `update()` throws
  it for its snapshot-level checks; the block's `checkTrust` calls keep the plain
  `TrustException`. `update()` is the choke point.
- Chrome: `TrustStoreError extends TrustError` (same user-facing message). `update()` tracks a
  `phase` flag: everything outside the operation call, including `keys.read()`, `local.read()`,
  post-operation validation and `local.write()`, becomes `TrustStoreError`; only failures from the
  operation's own checks and the fences keep `TrustError`.
Each repository takes an `onStoreFailure` callback, invoked once per failure episode at the
choke point, so a failure first observed in reconnect lookup, authentication metadata, pairing,
activation or the coordinator is routed the same way. The composition root wires it to the
coordinator's `failClosed()`, which reports the single status "re-pair required", never
suggests erasing trust automatically, and terminates every live session and pairing connection
asynchronously on the host scope, because existing sessions keep their keys and would otherwise
outlive the failure (D2). A peer-specific rejection during an ordinary revocation race never
disconnects unrelated peers. Tests: malformed serialized Android state, an invalid Chrome identity and a failing durable
write (Android and Chrome) each close live sessions of unrelated peers, and a peer-level
rejection closes none.
Android reset needs a primitive that works when the stored payload is unreadable. The Room
implementation decodes before running a transaction block, so `TrustStorage` gains
`suspend fun reset()`, which deletes the trust row (or the database file) without decoding it,
and `IdentityKeyStore` gains `delete()`, which removes the keystore alias. Order: terminate all
sessions and cancel pairings, delete the trust row, then delete the key. An interruption after
either deletion is still fail-closed (a snapshot without its key, or a key without its metadata,
both throw `TrustException` today) and `reset` is idempotent, so running it again completes it.
The next repository use creates a fresh identity. Chrome reuses `reset()`, which already writes an
invalid marker first. Reset is only ever invoked by an explicit user action.

**D7. Re-pairing.** After revocation (or reset) the normal OTP-10 enrollment applies unchanged.
The integration tests cover: revoke on the phone, then the Chrome side pairs again; a stale
Chrome record re-pairs only after the phone-side record is revoked (the spec's documented
consequence).

**D8. Concurrency: a per-peer fence plus a final sweep.** Android's pairing `Mutex` is private to
one connection, so it cannot serialize a revoke; this design does not rely on it. A deleted
record alone is not enough either: `addPending` creates an absent record, so an enrollment that
has authenticated the peer and not yet persisted would recreate it after the delete. Each
repository therefore gets an in-memory per-peer fence, taken inside the same transaction path
that `addPending` uses:
1. `beginRevocation(peerId)` marks the id; while marked, `addPending` for that id throws a
   peer-level rejection (Chrome: the existing generation fence is extended to check the mark
   immediately before the durable write).
2. Delete the record (D2).
3. `closePeer` terminates and awaits every bound generation or link (D3), so no in-flight work
   remains that could write.
4. A final **sweep** deletes the id again, removing anything that landed between steps 1 and 3;
   then `endRevocation(peerId)` clears the mark so a deliberate new pairing of the same
   installation ID can proceed.
When the revocation is driven from a handler (the mutual-removal responder), the handler runs
under the current generation's mutex, so it only takes the fence and deletes, then returns
`closeAfterReply`. The coordinator detaches a `finishRevocation(peerId)` job on the host scope
that awaits every other matching generation, then awaits the *current* generation's end (each
generation exposes an `ended` signal that completes when its teardown finishes, on Android a
`Live.ended` deferred and on Chrome the handle's closed promise), and only then runs the sweep
and `endRevocation`. The fence therefore outlives the handler's return and is never awaited from
inside it. A test asserts the fence is still held at handler return and released after the
current generation ends.
Later writes for the peer (`confirm`, `markAuthenticated`, activation) need an existing record
and throw once it is gone. The fence is not persisted: a restart has no in-flight work. The
interleavings are tests: revoke before `addPending`, between `addPending` and commit, between
commit and ack, during signed recovery, and concurrently with a duplicate commit; each must end
with no record and no live connection for that peer, and a later fresh pairing must succeed.

**D9. Debug-only controls.** The Android debug pairing screen and the Chrome debug pairing panel
get a peer list with Revoke, Mutual remove and Reset actions. They live in the existing debug
source set / dynamic import behind `__VELES_CHANNEL__ === 'development'`. New strings are added to
`releaseHookForbidden` and the bundle test's FORBIDDEN list so release artifacts are proven free of
them. No other UI is added.

**D10. Evidence.** Tests per endpoint: multi-peer repository, live-session revocation (only the
matching session closes), mutual and offline removal, corruption and key-loss, re-pairing
integration, listing, and guard tests. Removal runs through the existing scenario conformance
suites (`ScenarioConformanceTest.kt`, `session/scenarios.test.ts`) against the existing fixture.
Tests use injected clocks and never sleep.

## Risks / Trade-offs

- [A removal handler in production widens the application-record surface] → FULL-class only
  (existing policy), empty-body only (existing codec), requester state scoped to the session,
  unsolicited responses fail the session; covered by negative tests.
- [Delete-then-disconnect leaves a brief live session without a record] → the session is
  already authenticated and is closed immediately after; no new session can authenticate because
  the lookup reads the live store. Tested.
- [Reset on Android deletes the keystore alias, irreversible] → explicit user action only, debug
  control labels it destructive, never triggered by an error path.
- [Mutual removal leaves a stale record on the other side if the response is lost] → documented
  contract behavior; the UI wording says "removed here; the other device may keep its record".
- [Ordering bugs between revoke and in-flight pairing] → the per-peer fence, bound-connection termination and final sweep (D3, D8) plus tests
  that interleave them.

## Migration Plan

No data migration: no stored format changes. New code is additive; `IdentityKeyStore` gains a
method, `TrustStorage` gains `reset()`, `PairingConnection` gains `boundPeerId()` and
`ApplicationHandler` gains a defaulted `closeAfterReply` (all implementations and fakes are updated
in the same change). Rollback is a revert; no
persisted state is introduced.

## Open Questions

None blocking.
