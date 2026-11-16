## Design Summary

OTP-11 follow-up (issue #154): mutual removal can leave the two endpoints disagreeing when the
Android phone asks and the Chrome extension responds. The phone reports
`No response: the other device may keep its record` and keeps its trust record; Chrome deletes its
record and closes. The recorded device run (2026-11-15, OnePlus 6T vs the development extension and
the macOS Native Messaging host) saw this on the Android → Chrome row of
`docs/otp-11-debug-controls.md`; the Chrome → Android row passed on the same hardware.

The response is not delayed — it is dropped at teardown. On Chrome the responder sends
`removal_response`, waits for the native host's `sent` event, and then issues `disconnect`. `sent`
means the macOS host's `write_without_response` calls returned at the local OS API boundary; it is
explicitly not an over-air delivery acknowledgement (OTP2-BRD-022.5, OTP2-GAT-013). The host's
`disconnect` then cancels the link, and a BLE write that the OS had not yet transmitted is lost.
The phone, which only deletes after validating the response while its 10 000 ms deadline runs,
keeps its record.

The change makes the **Chrome responder hold the physical link after the response until the
requester closes it**, with a bounded fallback. The requester closes only after it validated the
response and deleted its own record, so its disconnect is the application-level delivery evidence
that `sent` cannot provide. The session still tears down logically, deletes trust, settles the
revocation fence and reports `removed` immediately; only the transport release is deferred. A peer
close finishes the hold locally with no disconnect request; a 5 000 ms injected timer bounds a
requester that never closes. No protocol, wire, fixture, native-host or Android change is needed.

## What the code does today

Observed, with the places it was observed:

- **Chrome responder, send.** The engine's reply-then-close path returns
  `[send(removal_response), terminated(STAGE_REMOVAL), close]`
  (`web-extension/src/session/engine.ts:1098-1111`, `terminalStage` at `:430`). The connector's
  session manager applies the send and awaits `controller.send`
  (`web-extension/src/connector/sessions.ts:431-447`), which resolves when the native port posts
  `sent` (`web-extension/src/connector/controller.ts:313`, `pumpSend`/`finishSend` at `:626-660`).
- **Chrome responder, close.** The `terminated` effect calls `SessionManager.terminate`
  (`sessions.ts:542-570`), which erases the engine keys, logs `session_terminated` and sends
  `controller.disconnect` fire-and-forget (`:568`). The `close` effect is behind it in the same
  effect list; `terminate` returns before it, so the disconnect is the only transport release.
- **Native host.** `Event::Sent` is emitted from `on_write_done`
  (`native-bridge/host/src/core.rs:1451-1462`) after the batch was submitted. The macOS backend
  writes with `write_without_response` (`native-bridge/host/src/macos.rs:676-689`), and a
  `disconnect` request drops the connection and cancels the link (`core.rs:1163-1265`,
  `macos.rs:1071-1088`). The WinRT path uses the equivalent local-completion semantics.
- **Protocol contract.** OTP2-BRD-022.5 says a `sent` confirms only that every native
  write-without-response submission returned successfully at the local OS API boundary — never an
  application or over-air acknowledgement. OTP2-GAT-013 says a completed GATT write or bridge
  `sent` confirms byte transfer only, never a delivery guarantee. OTP2-REC-013 defines the
  removal exchange: the responder deletes, sends `removal_response` and disconnects; the
  requester deletes after validating it.
- **Chrome requester.** On a delivered `removal_response`, the engine returns
  `[terminated(STAGE_REMOVAL), close]` and the session manager disconnects immediately
  (`sessions.ts:542-570`; the requester test at `web-extension/test/session/removal.test.ts:451-475`).
  This direction is correct: once the requester has the response, it needs nothing more.
- **Chrome recovery.** `ConnectorApp.handleChange` schedules an automatic reconnect for a handle
  that disappears while its previous status was `connected` or `ambiguous`
  (`web-extension/src/connector.ts:1475-1488`). Today the `terminate` disconnect sets the status
  to `disconnecting` before the link drops, so a successful removal never triggers recovery. A
  held handle would keep `connected` and, after the peer closes it, would schedule a `search`
  recovery for a peer whose trust record was just deleted. The hold must therefore mark the handle
  for the recovery skip before the close reaches `handleChange`.
- **Android responder.** `SessionHost.apply` executes `SessionEffect.Close` as
  `teardownLocked` plus `transport.close` (`android/.../sharing/SessionHost.kt:783-789`), and
  `SharingGattServer.close` flushes its pending send queue before `cancelConnection`
  (`.../SharingGattServer.kt:561-600`, `:760`). `SharingGattServer.pump` waits for each
  notification's completion before popping it (`:497-540`). This is why Chrome → Android passed.
- **Android requester.** It arms `REMOVAL_RESPONSE_TIMEOUT_MS = 10 000` on an injected clock
  (`android/.../sharing/SessionHost.kt:455-520`) and keeps the link while awaiting the response.
  Its `closeAfterReply` runs only after the response validated and deleted the record.
- **Device evidence.** `docs/otp-11-debug-controls.md` findings 2 and the
  `Mutual removal (live full session) | Android → Chrome` row: phone `No response…`, Chrome
  deleted and closed, phone kept its record. The opposite row passed.

## Alternatives Considered

### Alternative A: Hold the responder's link until the requester closes
- **Approach**: after the response's `sent`, the session manager tears the session down exactly as
  today but defers `controller.disconnect`; it subscribes to the existing `onConnectionClosed`
  notification and arms a 5 000 ms injected timer. A peer close clears the timer, unsubscribes and
  finishes with no disconnect request (the handle is already gone); timer expiry issues one
  disconnect. The requester closes only after validating the response, so the observed close is
  the delivery evidence the bridge cannot provide.
- **Pros**: observes actual delivery instead of guessing; no protocol, wire, fixture, native-host
  or Android change; mirrors the reviewed OTP-10 follow-up hold; the requester's own end-of-removal
  is already the last thing it does; logical teardown, trust deletion and the revocation fence are
  not delayed; all paths are testable with the existing injected `SessionTimers` and mock port.
- **Cons**: a responder link can live up to the 5 s bound after the record is gone; a requester
  that never closes (or whose close never propagates) falls back to today's disconnect; the hold is
  a new owner state that must be cleaned on port close/reset.
- **Why not chosen**: n/a — this is the chosen approach.

### Alternative B: Fixed linger in the native host before the disconnect
- **Approach**: after the final write's `WriteDone`, the host waits a constant (for example
  250-500 ms) before processing a `disconnect` request, giving the OS time to transmit.
- **Pros**: one place for every flow; no extension state.
- **Cons**: it guesses instead of observing; there is no signal that the OS finished transmitting;
  it changes the semantics of every disconnect (revocation, fail-closed, reset, link loss) and
  both platform backends; the same idea was rejected in the OTP-10 follow-up brainstorm because it
  converts a race into a smaller race.
- **Why not chosen**: no delivery evidence, widest blast radius, and it would also delay
  security-relevant teardown for unrelated flows.

### Alternative C: Write the terminal response with response
- **Approach**: submit `removal_response` with `write_with_response` and wait for the GATT ATT
  acknowledgement before closing; a true transport-level acknowledgement.
- **Pros**: strongest possible evidence short of an application record; no hold timer.
- **Cons**: the GATT profile and the bridge contract pin `write_without_response`
  (`SharingGattServer.kt:446` advertises `PROPERTY_WRITE_NO_RESPONSE`; the macOS connection proof
  rejects a characteristic without `write_without_response`), so the Android characteristic, both
  host backends, the protocol contract and its fixtures would change. That is a protocol change,
  not a follow-up.
- **Why not chosen**: out of scope for a narrow OTP-11 fix and it breaks the frozen transport
  contract.

### Alternative D: Requester-side retry or keep-alive
- **Approach**: have the requester resend `removal_request` or keep the link longer after a lost
  response.
- **Pros**: no responder change.
- **Cons**: the protocol forbids further records after removal in either direction, and the
  request already left the phone; a retry would be a new exchange on a new session against a peer
  that has already deleted its record. The requester cannot make the responder's OS transmit the
  response.
- **Why not chosen**: it cannot fix the loss and conflicts with OTP2-REC-013.

## Agreed Approach

Alternative A, chosen by the user after the reviewer recommended it. The mechanism, in outline —
`design.md` fixes the details:

1. **Responder detection.** `SessionManager.terminate` is the single owner of the transport release.
   The role is carried by the engine's terminal effect, because the engine knows which record
   triggered the reply-then-close path and tears its state down before the owner sees the effect.
   The `terminated` effect gains an optional `holdForDelivery: true`, set only when the closed
   record was a `removal_request` (the responder). A `removal_response` (the requester) and every
   non-removal close carry no flag. The owner treats `reason === STAGE_REMOVAL && holdForDelivery`
   as the responder and holds; everything else disconnects immediately. This is sturdier than
   reading `session.removal`, which is already set when `requestRemoval` queues its step and would
   misclassify a simultaneously queued peer request as a requester, and sturdier than reading the
   engine's `removalPending` after the step, because the engine resets that state during its own
   teardown. Android's engine is untouched.
2. **Deferred release.** The `closeLink` branch registers a hold instead of calling `disconnect`:
   one `onConnectionClosed(handle, …)` subscription, one `setTimeout` on the existing injected
   `SessionTimers`, keyed by the connection handle. The hold records the peer id for revocation
   cleanup.
3. **Peer close finishes locally.** The controller's existing handle diff answers immediately when
   the handle is already gone and notifies on every removal (loss, requested disconnect, reset,
   port close). The hold clears its timer, unsubscribes and sends no `disconnect` request.
4. **Bound expiry sends one disconnect.** `REMOVAL_DELIVERY_HOLD_MS = 5_000`, anchored on the
   post-`sent` teardown. It is well above the normal request/response/close path and below the
   requester's 10 000 ms deadline, so the fallback never outlives the phone's own bound.
5. **Logical teardown is unchanged.** Trust deletion, engine teardown, the `removal` settlement,
   `session_terminated` diagnostics, `session.closed`, the revocation fence and the UI outcome all
   run at the same point as today. Only the physical link release moves.
6. **No records are processed during the hold.** The session is already removed from `byHandle`
   and its engine is erased; inbound records are ignored exactly as after any close. The hold
   carries no keys and writes nothing. The connector snapshot keeps the handle `connected` for up
   to the bound, so the debug surface may show a live connection while the peer record is already
   gone; that transient display is accepted and no control depends on it.
7. **Recovery skip.** The held handle must not schedule an automatic reconnect when it disappears:
   `ConnectorApp.handleChange` treats a vanished `connected` handle as a recoverable loss
   (`connector.ts:1475-1488`). The session manager therefore reports a hold start through a narrow
   connector callback, and the connector puts the handle in a `removalHoldHandles` set; the first
   `handleChange` that sees the handle disappear consumes the entry and skips recovery. The
   callback is start-only, not a paired begin/end: the controller's close listeners run before the
   change listeners (`controller.ts:704-728`), so an end notification from the hold would remove
   the mark too early and let recovery run. The mark is not removed when a `closeAll`/`closePeer`
   finishes the hold; it is consumed by the handle's disappearance, so those disconnect paths also
   skip recovery. A later, unrelated recovery may still search for devices, but it cannot target
   the removed peer: its trust record is gone and `connectionPeerTargets` is pruned with the
   handle.
8. **Cleanup reaches held links.** `abortAll` (port close) clears every hold timer and
   subscription without issuing a disconnect. `closeAll` (identity reset/fail-closed) and
   `closePeer` (revocation) finish the holds they select with a disconnect: the held session is
   logically gone, but an explicit destructive action must not leave its link to the bound. This
   matches the OTP-10 hold, where a revocation closes a held link. The connector's
   `removalHoldHandles` set is cleared on a full connector reset/port close.
9. **Tests.** Extend `web-extension/test/session/removal.test.ts` (the existing production manager
   harness) with: the responder's terminal effect carries `holdForDelivery`; the requester's does
   not; responder sends the response and posts no disconnect; a peer close finishes without a
   disconnect; the hold timer fires exactly one disconnect; a requester still disconnects
   immediately; a simultaneously queued peer request is still held as a responder; a port close
   clears the hold without a request; a reset and a same-peer revocation finish the hold; the
   logical termination, trust deletion and `session.closed` are not delayed. Extend
   `web-extension/test/connector/connector-app.test.ts` with the recovery regression: after a peer
   close during the hold, no `start_scan`/`connect` recovery is posted. The existing "responder
   disconnects immediately" assertion changes to the new transport ordering. No test sleeps; every
   timer is fired.
10. **Acceptance.** A real-device run of at least ten Android → Chrome mutual removals on a FULL
    session, each followed by the same Chrome → Android direction, all reporting
    `Peer removed on both devices` with both peer lists empty. The native-port trace shows the
    phone's `disconnected` event before Chrome's release and no premature `disconnect` request;
    `docs/otp-11-debug-controls.md` records the run with no ids, keys or fingerprints.

## Key Decisions

- **Observed hold, not a linger.** The wait ends on the requester's own disconnect, which follows
  its validated deletion; the timer only bounds a lost response. A fixed delay was rejected for
  the same reason in the OTP-10 follow-up.
- **Chrome responder only.** Android's GATT server already flushes queued notifications before
  `cancelConnection` and the reverse device direction passes; changing `SessionHost`'s close path
  would add risk in a reviewed fail-closed area for no observed defect. The acceptance run keeps
  both directions honest.
- **The hold is owner state, not engine state.** The engine keeps its reply-then-close contract and
  only marks the terminal effect with whether the triggering record was a `removal_request`; the
  transport release is an owner concern, exactly like the existing fire-and-forget disconnect.
- **The role comes from the triggering record type, not from `session.removal` or the engine's
  post-step state.** The engine knows whether it answered a `removal_request` (responder) or a
  `removal_response` (requester) and has already reset its removal guard by the time the owner sees
  the effect. A queued peer request that races the user's own request is still classified as a
  responder, because the engine handled a request.
- **A successful removal never triggers automatic recovery.** The connector's recovery skip is
  driven by a start-only hold mark consumed when the handle disappears, because the controller's
  close listeners run before its change listeners.
- **5 000 ms bound on the injected clock.** Long enough for a busy phone to process the response
  and close, short enough that a lost response degrades to today's behaviour before the phone's
  10 000 ms deadline. Tests fire the timer; nothing sleeps.
- **The link carries no secret after the logical teardown.** Engine keys are erased, no session is
  addressable, and no record is accepted or sent during the hold.
- **The revocation fence is unaffected.** The detached finish job already awaits `session.closed`,
  which resolves in `terminate` before the hold begins. An explicit reset or revocation finishes
  the hold as a destructive action, not as part of the fence.
- **Scope:** `web-extension/src/connector/sessions.ts`, `web-extension/src/session/engine.ts`
  (the terminal-effect flag), `web-extension/src/connector.ts` (the recovery skip), their tests,
  and the device-run document. No protocol, `protocol/`, `native-bridge/`, `android/` or fixture
  change.
- **Out of scope:** a failed `removal_response` send still ends as `send_failed` after the
  responder deleted its record, which is existing behavior and has no delivery hold.
- **Recorded follow-up, not part of this change:** none. If the device run cannot reproduce the
  loss before the fix (the failing row is intermittent), the PR note records that the unit test is
  the regression proof and the device run is the acceptance evidence.

## Open Questions

- None blocking. The WinRT host path was not read in depth; the hold does not depend on what
  `sent` guarantees there, and the acceptance run is on macOS, which was read.
- **Assumption to confirm on the device:** the Android requester's `cancelConnection` reaches the
  macOS central promptly enough for the hold to end well before 5 s. The OTP-10 follow-up run
  (2026-11-16, `docs/otp-11-debug-controls.md`) already showed the phone's close reaching the
  macOS host for the enrollment hold, which supports the assumption. If it does not hold for
  removal, every removal still succeeds but the Chrome link silently takes the bound; the
  acceptance run must show the phone `disconnected` event and no client `disconnect` request.
