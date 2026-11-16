## Context

The OTP-10 enrollment connection ends when activation completes: the client verifies
`activation_complete`, persists the `active_activation_pending` mirror
(`web-extension/src/trust/repository.ts:111-124`), seals `activation_ack`, erases its enrollment keys
and marks its machine `active` and closed (`client-machine.ts:748-818`). The link then sends the ack
frame and stops in the same step (`link.ts:282-296`, `:412-422`), issuing a transport `disconnect`
immediately after the host reported `sent`.

`sent` means the fragment batch was submitted to the OS stack
(`native-bridge/host/src/core.rs:1451-1462`, `macos.rs:676-689`), not that the phone received it. Two
independent losses follow from closing that early: the controller's TX queue can be cut mid-record,
and — even when every byte arrived — the phone discards an inbound step queued before its
`Teardown` because `isCurrent` requires `!live.ended`
(`android/.../sharing/SessionHost.kt:349-358`, `:685-687`, `:1040`).

The phone's own end of the enrollment is the only available delivery evidence: it returns
`Outgoing.Disconnect` from `onActivationAck` only after the ack verified and the record became
`ACTIVE`, and `SharingGattServer.close` flushes its queue before `cancelConnection`
(`PhonePairingMachine.kt:446-478`, `SharingGattServer.kt:560-600`, `:757-761`).

Stakeholders: the Chrome extension (the only changed component), the Android app and the Native
Messaging host as peers in the acceptance run, and the Gate B transport validation that needs a
first reconnect carrying OTP data.

## Goals / Non-Goals

**Goals:**

- Make the first signed reconnect after an enrollment a FULL session in the normal case, by keeping
  the enrollment link alive until the phone ends it.
- Never report a completed enrollment as a failure, whatever teardown path runs during the hold.
- Keep the enrollment connection free of OTP data and free of enrollment material through the hold.
- Keep a held link reachable by revocation, fail-closed, reset and port death.
- Bound the hold so a lost ack degrades to today's behaviour instead of hanging a handle.
- Cover the change with unit tests that do not sleep, and a real-device acceptance run.

**Non-Goals:**

- No protocol, wire, fixture, limit or conformance-inventory change. `close_enrollment_connection`
  on the client `activation_complete` row stays the machine's logical close, which still happens.
- No Android, Native Messaging host, installer or packaging change.
- No fix for the phone dropping queued inbound steps before `Teardown` (defense in depth for the
  grace-expiry path); recorded as a follow-up.
- No fix for the sibling loss of a mutual-removal response before the responder closes; that has
  its own tracked task.
- No new user-facing surface; the only visible change is the debug panel's `finished` flag.

## Decisions

### D1 — The hold lives in `PairingLink`

`ClientMachine` is already correct after activation: keys erased, `state = 'active'`,
`closed = true`, and `onTick` ignores `active` (`client-machine.ts:397-402`). The connector owns the
attempt but not the transport. The link owns the handle and is the only place that knows the ack
just left. So the hold is link state, and `ClientMachine` is not changed.

*Alternatives:* holding in `ClientMachine` would put transport timing into a protocol state machine;
holding in `connector.ts` would duplicate the link's send knowledge and its teardown ordering.

### D2 — The close notification is derived from the connection set, not from one event

`PairingTransport` gains a required method:

```ts
onConnectionClosed(connectionHandle: string, listener: () => void): () => void;
```

`ConnectorController` implements it by keeping the last set of connection handles and diffing it
inside `emitChange`, notifying once for each handle that disappeared. `emitChange` runs after every
state mutation (`controller.ts:470-490`, and `handleClose` calls it around its reset), so the diff
also sees the removals that never arrive as a `disconnected` event: the send-reply close in
`finishSend` (`controller.ts:626`), the disconnect reply's `unknown_handle` branch
(`controller.ts:296-297`), the event-driven close inside `state.apply` (`state.ts:528`), an explicit
reset and a port close. Enumerating event types would miss those; a missed removal would cost the
full grace, not correctness, but the diff is both simpler and complete.

The subscription answers immediately when the controller no longer holds that handle, so a close
that lands between the `connected` event and the link's subscription is not lost. The link
subscribes in `start()` next to `onRecord`, not when the drain begins, because the phone can close
before the ack's `sent` resolves.

*Alternative:* having the link diff `controller.onChange` snapshots — rejected as more state in the
link for the same information.

### D3 — Drain armed outside the frame queue, anchored on the ack's send

After the frames of a step are sent, the link stops as today unless the machine is
completed-active; then it arms the drain and the queue item returns. `terminate()` waits on that
queue (`link.ts:375-385`), so arming must not await the grace or a revocation/reset would be
delayed.

- `ACTIVATION_HOLD_MS = 5_000`, measured from the ack's successful `send`. It sits far above the
  phone's ack path and well below the phone's 30 s request-inactivity timeout
  (`android/.../pairing/PairingLimits.kt:24`), so the client never outlives the phone's own bound.
- `setTimeout`/`clearTimeout` are injected like `setInterval`/`clearInterval`, and `clearTimers()`
  (renamed from `clearTimer`) clears both the tick and the drain timer on every teardown.
- Arming unsubscribes `onRecord`, and the queued handler drops any record already queued. No inbound
  frame reaches the machine during the hold: a phone that rejected the ack answers `preauthReject`
  and disconnects (`PhonePairingMachine.kt:596-601`), which would turn a committed record into a
  failed one. Dropping is done by a `draining` flag, **not** by `stopped`, because `terminate()`
  returns early on `stopped` (`link.ts:369`) and must still close a held handle. The flags and what
  reads them:

  | Field | Set by | Read by |
  | --- | --- | --- |
  | `disposed` | `dispose()` | `receive`, `terminate` |
  | `stopped` | `stop()`, `cancel()`, the peer-close finish | `receive`, `terminate` |
  | `draining` | arming the drain | `receive` (drop the record) |
  | `peerClosed` | the close notification, at any time | arming the drain, the finish |
  | `ended` | `notifyEnded()` | — (one-shot) |

- The `peerClosed` latch is set by the close notification at any time. It never acts on its own
  before activation; with the latch set, arming the drain finishes at once instead of waiting the
  grace.
- A close observed while the ack's send is still pending usually fails that send instead: an applied
  `disconnected` calls `closeConnection`, which releases queued sends (`state.ts:407-413`). That
  takes D4's failed-send path, whose `disconnect` is answered locally with `unknown_handle`
  (`controller.ts:279-281`) and puts no request on the wire.

### D4 — Terminal-active rule, and a held link stays reachable

The link defines `activationCompleted()` as `machine.closed && machine.state === 'active'`. Once it
holds, no path calls `machine.onDisconnect()`: that call erases a live attempt and marks failure
(`client-machine.ts:387-394`), and there is nothing live and no failure to report. Concretely:

- `terminate()` still fences, queues behind the frame queue, erases and closes, but skips
  `onDisconnect()` for a completed activation. This is the fail-closed, reset and port-death path.
- A final send that throws ends through the existing `stop()` — including its `disconnect` request
  against a possibly dead handle, so no handle leaks — but also skips `onDisconnect()`. The
  enrollment is left to the recovery protocol, exactly like grace expiry.
- Grace expiry tears down as today and the machine stays `active`.

**Reachability.** `closePeer` selects enrollment links by `link.boundPeerId() === peerId`
(`web-extension/src/connector/sessions.ts:355-361`), and activation clears that id because
`#onActivationComplete` calls `#eraseLive()`, which nulls `#boundPeerId` (`client-machine.ts:814`,
`:948`). A held link would therefore be invisible to a peer revocation.

The link therefore keeps the id for the length of the hold: it reads `machine.boundPeerId()` after
every step that can set it — after `machine.onFrame(...)` returns and after `machine.commit()`
(`client-machine.ts:634-639`) — keeps the last non-null value, and answers its own `boundPeerId()`
with `machine.boundPeerId() ?? remembered` once `activationCompleted()` is true. On every other path
it answers the machine's value unchanged, so a failure that erases the binding
(`client-machine.ts:634-638`) is never resurrected. Sampling lazily inside the accessor would not
work: nothing calls it before activation. `ClientMachine` is untouched. `closeAll()` (reset,
fail-closed) and `abandon()` (port death) already reach the link by handle.

`cancel()`, `dispose()` and the in-queue abort are unchanged and end the link immediately. On a
completed activation `dispose()`/`cancel()` set the machine back to `idle` without a failure
(`client-machine.ts:865-872`), because `#pendingPeerId` is already `null`; this is accepted: the
durable mirror is untouched, no error is reported, and the phase reads `idle` only because the
attempt is over. A new pairing attempt replaces the link through `teardownPairing()` as before.

### D5 — Finishing paths, and unsubscribing before disconnecting

`stop()` gains an option to skip `controller.disconnect`. On the peer-close path the handle is
already gone, so the link clears its timers, unsubscribes both listeners, sets `stopped`, and calls
`notifyEnded()` without a disconnect request; the connector's `handleChange` removes the handle from
`pairingHandles` without scheduling recovery (`connector.ts:1456`). The grace-expiry and failed-send
paths keep the disconnect request.

Every teardown unsubscribes the close notification as well as `onRecord`: `stop()`, `dispose()`,
`terminate()` and the peer-close finish. `stop()` unsubscribes **before** it issues the disconnect,
so the `disconnected` reply to the link's own request cannot re-enter the close path.

### D6 — A port close ends a held link

`handleClose` already ends the attempt through `abandonPairing()`/`abandon()`
(`connector.ts:882-889`, `:1365`), which reaches `terminate()` and therefore D4. As a second guard,
the D2 sweep notifies every handle that the reset drops, so a link that somehow outlives the attempt
teardown still finishes. Both are idempotent; the link's `ended` flag makes `notifyEnded` run once.

### D7 — The panel reads the machine's terminal state

`pairingState.finished` becomes `pairingEnded || machine.closed`, so a held link does not report
"not finished" for up to the grace. `pairingFinished` keeps meaning "the attempt ended"; its only
reader is a test (`test/connector/revocation-integration.test.ts:1498`), while the debug panel reads
`pairingState.finished` (`web-extension/src/debug/pairing-panel.ts:155`).

### D8 — Tests and their seams

- `PairingLink.start` gains `setTimeout`/`clearTimeout` deps. `ConnectorAppDeps` gains
  `setInterval`/`clearInterval` next to its existing `setTimeout`/`clearTimeout`
  (`connector.ts:225-226`), and the connector forwards the complete timer set when it starts the
  link (`connector.ts:648`), continuing to pass no `tickIntervalMs`. The link keeps its own defaults
  for callers that pass no timers.
- The new method goes on `ConnectorController` and on the transport-facing surfaces test doubles are
  typed by (`SessionController`/`ConnectorControllerLike`, `sessions.ts:66-78`, `connector.ts:186`),
  so every implementer updates in the same task: `FakeTransport`
  (`test/pairing/client-machine.test.ts:190`), `DeferredPairingController` (`:277`), the connector
  fakes in `test/connector/close-peer.test.ts` and `test/connector/revocation-integration.test.ts`,
  and `LoopbackPairingTransport`.
- Existing tests that reach activation: `enrollment_full` (`test/session/loopback.test.ts:1143`)
  makes its transport fire the phone's close, which is what the real phone does;
  `interrupted_activation_resumes` (`:1228`) stays in `awaiting_complete`, and its `disconnect`
  command only sets the `peerClosed` latch. In `test/connector/revocation-integration.test.ts` the
  helpers that drive a link to `active` — `driveToActive` (`:514-522`, used at `:994` and `:1010`)
  and the `pairThroughApp`/`ackDisconnect` path (`:1451-1460`, used at `:1934-1938` and `:1993`) —
  end the hold the way a phone does: their fake sends the `disconnected` event for the handle after
  the ack. The links those helpers start receive injected timers, so a close that is missed advances
  the grace instead of waiting on a real one, and `ackDisconnect` waits for that event or the timer
  rather than for a client disconnect request that no longer goes out.
- New coverage: peer close during the hold finishes without a disconnect request and with no
  recovery scheduled; grace expiry issues the disconnect once and leaves the machine `active`; a
  `preauthReject` arriving during the hold does not flip the machine to failed; `terminate()` during
  the hold closes without `onDisconnect()`; a revoked peer closes a held link; a completed
  activation reached through a failed final send stays `active`; the panel reports finished while
  the link is held.
- The conformance and enrollment scenarios keep observing `machine.closed` and must pass unchanged.
- Edge paths that cannot be triggered by hand on a device (cancel, tab close and revocation exactly
  inside a sub-second hold) get the same escape hatch as grace expiry: unit-tested only, stated in
  the PR note.

## Risks / Trade-offs

- **The phone's close may not reach the client promptly** (Android `cancelConnection` on a
  central-opened link; CoreBluetooth `didDisconnectPeripheral`; WinRT unread) → every enrollment
  would still succeed but silently pay the 5 s grace. Mitigation: the acceptance run reads the
  native-port trace for a phone `disconnected` event with no client `disconnect` request, and the
  panel's finished timing shows it.
- **A peer close proves the phone finished, not that it accepted the ack.** On a rejected ack the
  phone revokes its record only when the MAC fails (`eraseIncompleteFailure`,
  `PhonePairingMachine.kt:481-495`); a store read, state mismatch or failed persist takes plain
  `reject()` (`:596-601`) and leaves it `committed_waiting_ack`. Either way the client drops the
  reject, finishes, and keeps an `active_activation_pending` mirror for a record the phone will not
  activate. That is the same end state as grace expiry and is covered by the protocol's
  unknown-installation path (OTP2-REV-001), re-pair after the mirror is revoked. The acceptance run
  distinguishes the cases by the phone log: `ACTIVE` before the close means accepted.
- **A BLE handle lives up to the grace after activation** → it carries no key material and no
  record change, and revocation, fail-closed, reset and port death still terminate it; covered by
  tests including the revoked-peer case.
- **The hold changes teardown timing for paths that used to be unreachable after activation** →
  the terminal-active rule, the flag split and the reachability decision are the whole defence, and
  each path has a test.
- **A new required interface method breaks every implementer** → the method is added to the
  controller, to the transport-facing interfaces and to every double, including
  `DeferredPairingController` and `LoopbackPairingTransport`, in the same task; typecheck is part of
  that task's verification.
- **A lost ack is still possible** (grace expiry, or the phone's worker never runs) → the recovery
  protocol remains the backstop; this change narrows the window instead of removing it, and it never
  reclassifies a completed activation.

## Migration Plan

No data migration and no rollout flag: the change is behavioural inside one extension build.
Rollback is a revert of the commit; the durable records written by either version are the same
`active_activation_pending` mirror and `committed_waiting_ack`/`active` phone records, and the
recovery protocol covers enrollments started under either.

## Open Questions

- The WinRT `write_batch` path was not read. It cannot change the design, because the hold does not
  rely on what `sent` guarantees.
- The phone-side inbound drain before `Teardown` needs a tracked task before it can be planned.
