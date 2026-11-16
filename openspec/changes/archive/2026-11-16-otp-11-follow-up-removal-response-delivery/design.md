## Context

The mutual-removal exchange (OTP2-REC-013) is the only OTP-11 operation where one endpoint deletes
trust and then must tell the other endpoint that it did. The responder deletes its record, sends
`removal_response` as the last record in that direction, and closes. The requester deletes only
after validating the response; if the response is lost, it keeps its record and reports the
offline outcome.

On the Chrome endpoint the response is a GATT write submitted by the native host with
`write_without_response`. The host's `sent` event means every fragment submission returned at the
local OS API boundary; the protocol is explicit that this is never an over-air acknowledgement
(OTP2-BRD-022.5) and never a delivery guarantee (OTP2-GAT-013). The connector nevertheless treats
`sent` as the end of the exchange: `SessionManager.apply` awaits `controller.send`, then the
engine's `terminated` effect drives `SessionManager.terminate`, which fire-and-forgets
`controller.disconnect`. The host cancels the link, and a write still in the OS transmission queue
is dropped.

The 2026-11-15 device run recorded the result: an Android requester pressed `Mutual remove`, Chrome
deleted its record and closed, the phone reported `No response: the other device may keep its
record` and kept its trust. Chrome → Android passed on the same hardware because the Android GATT
server flushes its notification queue before `cancelConnection`.

Stakeholders: the Chrome extension (the only changed component), the Android app and the macOS
Native Messaging host as peers in the acceptance run, and the debug-controls document that records
the evidence.

## Goals / Non-Goals

**Goals:**

- Make the Android → Chrome mutual removal complete on both endpoints in the normal case, by
  keeping the responder's link alive until the requester's own end-of-removal is observed.
- Keep the response as the last record, trust deletion immediate, the revocation fence immediate
  and the reported outcome immediate; defer only the physical transport release.
- Bound the deferral so a lost response or a requester that never closes degrades to today's
  behaviour instead of holding a link indefinitely.
- Cover the change with deterministic tests on the injected timers and a real-device acceptance run
  of both directions.
- Keep a successful removal from scheduling an automatic reconnect for the deleted peer.
- Keep the change out of the protocol, the wire, the fixtures, the native host and Android.

**Non-Goals:**

- No protocol, wire, fixture, limit or conformance-inventory change. `removal_response` stays the
  last record and no new record, ack or retry is introduced.
- No native-host or Android behaviour change. The host keeps its local-completion `sent` and its
  disconnect semantics; Android keeps its queue flush.
- No change to the requester's 10 000 ms deadline, the requester's fencing or the offline-removal
  semantics.
- No new user-facing surface or debug control. The device run reads the existing outcome lines and
  the native-port trace.
- No fix for a requester whose close never propagates; that path takes the bounded fallback.

## Decisions

### D1 — The hold is session-manager state, not engine state

The engine already produces the correct logical order: `[send(removal_response),
terminated(STAGE_REMOVAL), close]` (`web-extension/src/session/engine.ts:1101-1112`). The gap is
the owner's transport release, not the engine's effect list. The hold therefore lives in
`SessionManager` (`web-extension/src/connector/sessions.ts`), next to the existing fire-and-forget
disconnect at `:568`. The engine gains one optional field on the terminal effect (D2) to say which
record triggered it; the handler interface and Android are untouched.

*Alternatives:* an engine-level hold flag on the `close` effect would require reordering the
terminal settlement; asking the engine for its removal guard after the step cannot work because
the engine resets it during its own teardown; a host-level linger would guess and change every
disconnect (rejected in the brainstorm).

### D2 — The engine marks the terminal effect with the triggering record

The engine is the only place that knows which record caused the reply-then-close path, and it
tears its own state down (the `#teardownInternal()` calls at `engine.ts:1086` and `:1106`) before the owner applies
the effects. A post-step read of the removal guard therefore cannot distinguish the roles; it is
already reset. The `terminated` effect gains one optional field:

```ts
| { kind: 'terminated'; reason: string; holdForDelivery?: true }
```

The close-after-reply branch sets it only when `opened.type === RecordType.REMOVAL_REQUEST` and the
handler did not fail:

```ts
{
    kind: 'terminated',
    reason: terminalStage(terminalError, opened.type),
    ...(!terminalError && opened.type === RecordType.REMOVAL_REQUEST
        ? { holdForDelivery: true }
        : {}),
}
```

`SessionManager.apply` passes `effect.holdForDelivery === true` to `terminate`; the owner holds
only when `reason === STAGE_REMOVAL && holdForDelivery`. A `removal_response` (the requester), an
`app_error` termination and every non-removal close carry no flag and disconnect immediately.

This is sturdier than reading `session.removal`. `requestRemoval` registers the exchange before its
queued `startRemoval` step, so an inbound `removal_request` that is already queued runs first and
is answered as the responder while `session.removal` is non-null; the triggering record is still a
request, so the hold is created correctly. A session whose guard was already set can never accept
a `removal_request` (`acceptsDuringRemoval` at `engine.ts:1004`); it ends with `authorization`
instead, so no hold is created for a requester. The requester's exchange is settled by the same
`STAGE_REMOVAL` termination (the local record was deleted by the incoming request), which is the
correct local outcome. Both roles and the simultaneous-request race are covered by tests.

### D3 — One hold per connection handle, on the existing injected timers

`SessionManager` gains:

```ts
export const REMOVAL_DELIVERY_HOLD_MS = 5_000;

interface RemovalHold {
    peerId: string;
    timer: unknown;
    unsubscribe: () => void;
}
private readonly holds = new Map<string, RemovalHold>();
```

The `closeLink` branch of `terminate` becomes: call `holdOpen(session)` when the responder rule
holds, otherwise `controller.disconnect` as today.

- `holdOpen` registers `controller.onConnectionClosed(handle, () => finishHold(handle, false))`
  and arms `timers.setTimeout(() => finishHold(handle, true), REMOVAL_DELIVERY_HOLD_MS)`.
  `onConnectionClosed` already answers immediately when the controller no longer has the handle and
  notifies on every removal — the requested disconnect, a loss, a reset and a port close
  (`web-extension/src/connector/controller.ts:419-437`).
- `finishHold(handle, disconnect)` is idempotent: delete the map entry, clear the timer, unsubscribe,
  and only when `disconnect` is true and the port is open call `controller.disconnect(handle)`.
  The expiry path may race a handle that is already gone; the controller answers that locally as
  `unknown_handle` and the call is swallowed exactly like today's fire-and-forget close.
- A peer close finishes locally and sends no disconnect request: the handle is already gone, and
  issuing a request would only produce a local `unknown_handle`.

The 5 000 ms bound is measured on the injected `SessionTimers`, so tests fire it directly; it is
well above the normal request/response/close path and below the requester's 10 000 ms deadline, so
the fallback never outlives the phone's own bound. The constant is exported for the tests and the
debug documentation, like `REMOVAL_RESPONSE_TIMEOUT_MS`. If the controller is already closed when
`terminate` runs, no hold is created: there is no port to release and `abortAll` handles the port
close path.

### D4 — A successful removal must not schedule automatic recovery

`ConnectorApp.handleChange` schedules a reconnect for a handle that disappears while its previous
status was `connected` or `ambiguous` (`web-extension/src/connector.ts:1475-1488`). Today the
fire-and-forget `terminate` disconnect sets the status to `disconnecting`
(`web-extension/src/connector/state.ts:378-385`), so the disappearance is skipped. A held handle
stays `connected`, and the peer's close would otherwise trigger a `search` recovery for a peer
whose trust record was just deleted.

The session manager therefore takes a narrow, optional hold-start callback:

```ts
onRemovalHold?: (handle: string) => void;
```

`holdOpen` calls it once; `ConnectorApp` adds the handle to a private
`removalHoldHandles: Set<string>`. `handleChange` starts its disappearance loop with:

```ts
if (this.removalHoldHandles.delete(handle)) continue;
```

before the pairing/user-disconnect/recovery checks. The callback is deliberately start-only: the
controller's close listeners run **before** its change listeners (`controller.ts:704-728`), so an
end notification from the hold would remove the mark before `handleChange` could consume it. The
entry is removed when the handle disappears, and the connector clears the set on a full
reset/port close. The bottom cleanup loops of `handleChange` also prune `removalHoldHandles` for
handles not present in `next`, so a mark added after the handle already left `trackedConnections`
does not survive. A handle whose hold ends without disappearing (a refused disconnect) stays
marked, which is correct: the peer's record is gone and automatic recovery must not resurrect it.

### D5 — Logical teardown and the revocation fence are untouched

`terminate` keeps its current order: cancel timers, settle the removal (the requester's `removed`,
the responder's no-op), run `onSessionClosed`, erase the engine, remove the session from
`byHandle`, resolve `session.closed`, log `session_terminated` and settle the waiters. Only the
final `controller.disconnect` is deferred; the preceding `settle(...)` is not a transport action.
Concretely:

- `removal` is reported as `removed` at the same moment as today.
- The detached revocation finish job already awaits `session.closed`, so the fence, the sweep of
  the peer's other sessions and the fence release do not wait 5 s.
- `authenticatedSessions()` drops the session immediately; the UI sees the same outcome.
- The engine keys are erased before the hold, so the held link carries no session material and no
  record is accepted: `handleRecord` finds no session (the handle was removed from `byHandle`) and
  the engine is closed.
- The connector snapshot keeps the handle `connected` for up to the bound. That transient debug
  display is accepted: the durable peer list is authoritative, the peer row is gone, and no
  control reads the live-session marker for correctness.

### D6 — Cleanup paths

- **Port close:** `abortAll` (the `controller.onClose` listener) clears every hold timer and
  subscription with `finishHold(handle, false)`. The port is gone, so no disconnect is attempted;
  the connector clears `removalHoldHandles`.
- **Peer revocation of the same peer:** `closePeer(peerId)` must finish the holds whose stored
  `peerId` matches, with a disconnect — but **only holds that exist when the call is entered**.
  `closePeer` snapshots the matching holds synchronously, before it awaits any session or link
  teardown. This is required because the responder's own removal handler calls
  `deleteFenced(peerId)` (`web-extension/src/session/removal.ts:89`), which starts the detached
  revocation finish → `sessions.closePeer(peerId)` (`revocation.ts:205-246`) while the handler step
  is still running, before the responder's hold exists. The queued `closeBehind` job then sees a
  session already `closed` and returns; the hold created later by that same step must not be
  released by the revocation finish. A later user-initiated `closePeer` sees the hold and finishes
  it. A test with the production revocation coordinator (or the connector-app harness) asserts no
  `disconnect` is posted between the response's `sent` and the phone's `disconnected`; the
  `RecordingOperations` fake in `removal.test.ts` cannot catch this, because it never calls
  `closePeer`.
- **Identity reset / fail-closed:** `closeAll` likewise snapshots the holds at entry and finishes
  those with a disconnect. A hold created by a removal step that is still in flight when the reset
  is admitted did not exist at the snapshot and survives the reset to the bound; it carries no
  keys, accepts no records and is bounded, so that divergence is accepted. This matches the
  OTP-10 hold's rule that a destructive action must not leave an *existing* held link to the
  bound, without letting the responder's own revocation finish kill its response hold.
- **A second removal attempt:** `requestRemoval` finds no open FULL session for the peer (the
  responder session is gone) and returns `kept-not-connected`; no second hold can be created for
  the same handle because the handle is removed from `byHandle` before the hold.
- **A response send failure:** the engine terminates with `send_failed`, not `STAGE_REMOVAL`, so
  no hold is created and the disconnect is immediate as today. The responder has deleted its
  record and the requester keeps its own — existing behavior, out of scope.
- **A handler/deletion failure:** the engine terminates with `dependency`; no hold; fail-closed
  behavior unchanged.

### D7 — Tests and their seams

Extend `web-extension/test/session/removal.test.ts`, which already builds the production
`SessionManager` over the shared scripted phone, the real codecs and the mock native port, and
already records injected timers (`RemovalTimers`):

- **Responder holds:** emit a `removal_request`, receive the `removal_response`, ack its `sent`,
  flush, then assert no `disconnect` was posted and one timer with the new delay is pending. The
  engine's responder effects carry `holdForDelivery: true`.
- **Peer close finishes without a request:** fire the `disconnected` event for the handle, flush,
  assert the hold timer is cancelled, no `disconnect` was posted, and the revocation fence and
  `session.closed` already completed.
- **Bound expiry:** fire the hold timer, flush, assert exactly one `disconnect` request.
- **Requester unchanged:** the existing `settles removed only after a valid removal_response` test
  keeps asserting the immediate disconnect after the response, and the engine's requester effects
  carry no `holdForDelivery` flag.
- **Simultaneous request race:** queue an inbound `removal_request`, then call `requestRemoval`
  before the queue drains; the inbound step answers first and the responder hold is created even
  though `session.removal` is non-null.
- **Port close:** disconnect the mock port while held; assert the timer is cancelled and no
  `disconnect` was posted.
- **Reset and same-peer revocation:** call `closeAll`/`closePeer` while held; assert the hold
  finishes with a disconnect and the timer is cleared.
- **Logical teardown not delayed:** assert `ops.deleted`, `terminations(...) === ['removal']`, the
  removal result and `authenticatedSessions()` before the hold is released.
- **No record processing:** after the response, an inbound record on the held handle is ignored and
  does not create a disconnect or a diagnostic beyond the existing terminal one.

Extend `web-extension/test/connector/connector-app.test.ts` with the removal regression: establish
a session, answer a phone `removal_request`, ack the response, and assert that no `disconnect`
request is posted between that `sent` and the phone's `disconnected` event; then emit the phone's
`disconnected`, advance the connector clock and flush, and assert no `start_scan` or `connect`
recovery is posted. The no-disconnect half is what catches the responder's own revocation finish
releasing the hold; the recovery half alone would still pass because a premature `disconnect`
also sets the status to `disconnecting`. The existing recovery test at `:485` is the template, and
this test uses the production revocation coordinator and removal handler, unlike the
`RecordingOperations` fake in `removal.test.ts`.

The existing responder test at `removal.test.ts:476-498` currently waits for
`nextDisconnectFor`; it changes to the hold assertions. No test sleeps or reads a wall clock.

The transport-level evidence the issue asks for is the manager/port test: it exercises the actual
`send` → `sent` → `terminated` → `disconnect` ordering the device run sees, not only the engine's
effect list. The device run supplies the end-to-end evidence.

### D8 — Device acceptance and documentation

Update `docs/otp-11-debug-controls.md` with a new follow-up run section. Procedure:

1. Pair the OnePlus 6T debug APK with the development-channel extension and the macOS host.
2. Open a FULL session (disconnect and reconnect first if the row reports a recovery session).
3. On the phone, press `Mutual remove`; record the phone outcome line and both peer lists.
4. Repeat at least ten times, reconnecting between attempts.
5. Repeat the opposite direction (Chrome `Mutual remove`) at least ten times.

PASS for each Android → Chrome attempt: phone `Peer removed on both devices`, Chrome `Peer removed
on both devices`, both peer lists empty. The native-port trace shows the response's `sent`, the
phone's unsolicited `disconnected`, and no client `disconnect` request before that event. If the
phone's close does not propagate, the Chrome side takes the 5 s bound and the run records that as a
fallback, not as a PASS of the primary path.

Record only screen values and trace facts: no installation ids, keys, fingerprints or transcripts
(per the existing document's limits). The run section states that the expiry fallback is
unit-tested only unless the device actually exercises it.

### D9 — Rejected alternatives, recorded

- **Host linger:** a fixed 250-500 ms wait after `WriteDone` before `disconnect` would still guess
  and would change every disconnect in a security-sensitive path. Rejected for the same reason in
  the OTP-10 follow-up brainstorm.
- **GATT write-with-response:** true ATT evidence, but the profile and the Android characteristic
  advertise `PROPERTY_WRITE_NO_RESPONSE` only, and both host backends plus the frozen contract
  would change. Rejected as a protocol change, not a follow-up.
- **Requester retry:** forbidden by OTP2-REC-013 (no records after removal) and impossible after
  the responder deleted its record. Rejected.
- **Android hold mirror:** no observed Android defect; its GATT server already flushes before
  `cancelConnection`, and the reverse direction passes. Adding a second hold would broaden a
  fail-closed change without evidence. Rejected.

## Risks / Trade-offs

- **The requester's close may not reach the Chrome central promptly** (Android
  `BluetoothGattServer.cancelConnection` on a central-opened link; CoreBluetooth
  `didDisconnectPeripheral`; the WinRT path was not read in depth) → every removal would still
  succeed but silently take the 5 s bound. Mitigation: the acceptance run reads the native-port
  trace for the phone's `disconnected` event before Chrome's release; if it is missing, the run
  records the fallback and the PR note says so. The 2026-11-16 OTP-10 follow-up run already showed
  the phone's close reaching the macOS host for the enrollment hold, which supports the assumption.
- **A held link lives after the record is gone** → it carries no keys, accepts no records, has no
  session in `byHandle`, and is dropped by port close; `closeAll` and `closePeer` finish it
  explicitly. The revocation fence and the outcome do not wait on it. Covered by the cleanup tests.
- **Automatic recovery could resurrect a removed peer** → the connector's start-only
  `removalHoldHandles` mark makes `handleChange` skip the vanished held handle; the connector-app
  regression test asserts no `start_scan`/`connect` is posted after the peer close. The mark is
  start-only because the controller's close listeners run before its change listeners.
- **The role must survive the engine's own teardown** → the engine marks the terminal effect with
  the triggering record type before it resets its state; the owner does not read post-step state.
  The requester test, the responder test and the simultaneous-request race test cover both roles.
- **The snapshot keeps the handle `connected` during the hold** → the debug surface may show a
  live connection while the peer record is gone for up to 5 s; accepted, because the durable peer
  list is authoritative and no control reads the marker for correctness.
- **A lost response still leaves the two sides disagreeing** → this is the protocol's documented
  offline-removal outcome; the change narrows the window (normal delivery is observed) and never
  claims the response was delivered when the bound expires.
- **Timer throttling in the service worker** → the 5 s fallback may fire late, but a late fallback
  only holds a dead link longer; the primary path is the peer close, not the timer.
- **A second removal or a revocation racing the hold** → `byHandle` no longer contains the session;
  the hold map is keyed by the handle, `finishHold` is idempotent, and `closeAll`/`closePeer`
  finish the selected holds, so the first outcome wins.

## Migration Plan

No data migration and no rollout flag: the change is behavioural inside one extension build.
Rollback is a revert of the commit. The durable records written by either version are the same:
the responder has already deleted its record, the requester deletes on a validated response or
keeps it on a lost one. No protocol version, fixture or stored field moves.

## Open Questions

- None blocking. The WinRT `write_batch` path was not read in depth; the hold does not depend on
  what `sent` guarantees there, and the acceptance run is on macOS, which was read.
- The response-timeout exercise in `docs/otp-11-debug-controls.md` still needs a controlled
  response-suppression method; this change does not provide one and leaves that exercise pending.
