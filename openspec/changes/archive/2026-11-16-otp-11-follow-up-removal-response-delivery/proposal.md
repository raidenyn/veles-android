## Why

A mutual removal started from the Android phone can leave the two endpoints disagreeing: Chrome
deletes its record and closes, the phone keeps its record and reports
`No response: the other device may keep its record`. The device run recorded in
`docs/otp-11-debug-controls.md` hit this on the Android → Chrome row, while Chrome → Android passed.
The response appears to be dropped at teardown, not delayed: the connector waits for the native
host's `sent` (local OS submission only, never an over-air acknowledgement — OTP2-BRD-022.5,
OTP2-GAT-013) and then disconnects, so a write still in the OS transmission queue can be lost.
The diagnosis is inferred from the code and the run; fixing it makes the normal removal complete on
both sides and removes a manual recovery step.

## What Changes

**Responder transport release**
- From: Chrome sends `removal_response`, waits for the host's `sent`, and disconnects immediately;
  the macOS host can cancel the link before the BLE write is transmitted.
- To: the responder keeps the physical link after the response until the requester's own
  disconnect (the requester closes only after validating the response and deleting its record), or
  a 5 000 ms injected bound. Logical teardown, trust deletion, the revocation fence and the
  reported outcome stay immediate; only the transport release is deferred. A peer close finishes
  locally with no disconnect request; expiry issues one.
- Reason: in the normal case the requester's close follows its validated deletion and its local
  record removal, so it is the only evidence the responder can observe; the bridge's `sent` cannot
  provide it, and a close for another reason is bounded by the same hold.
- Impact: non-breaking. The response remains the last record; the protocol, wire, fixtures, native
  host and Android are unchanged.

**Recovery skip for the removed peer**
- From: a held handle that the peer closes would look like a recoverable `connected` loss and
  schedule an automatic `search`.
- To: the connector marks a held handle and skips automatic recovery when it disappears.
- Reason: the peer's trust record was deleted; reconnecting to it is wrong.
- Impact: non-breaking connector behavior; covered by a connector-app regression test.

**Terminal-effect role flag**
- From: the connector cannot tell whether a `STAGE_REMOVAL` termination answered a request
  (responder) or a response (requester) after the engine has torn down.
- To: the engine marks the `terminated` effect with `holdForDelivery: true` only for a successful
  `removal_request` close; the owner holds only for that flag.
- Reason: the role must be captured before the engine resets its guard; `session.removal` is
  already set when a simultaneous request is queued.
- Impact: extension-internal effect shape only; Android's engine is untouched.

**Cleanup paths for a held link**
- From: `closeAll` and `closePeer` only see live sessions; a held responder link would be invisible
  to an identity reset or a later revocation.
- To: both snapshot the matching holds synchronously at entry and finish them with a disconnect;
  the responder's own revocation finish (started while its handler step is still running) does not
  see the hold created later by that step, so it cannot cut the response. A hold created by a
  removal step that is still in flight when a reset is admitted survives that reset to the bound;
  it carries no session keys and accepts no records.
- Reason: a destructive action must not leave an *existing* held link to the bound, while the
  responder's own finish must not undo the delivery hold.
- Impact: non-breaking; cleanup ordering only.

**Out of scope:** a failed `removal_response` send (existing `send_failed` behavior); a requester
whose close never propagates (bounded fallback); the Android responder (already flushes its
notification queue); any protocol, bridge, installer or Android change.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `mutual-peer-removal`: the responder's close is now delivery-observed rather than local-`sent`
  observed. The existing "Removal request handling" and "Close after reply" requirements gain the
  transport-release rule, and a new requirement covers the bounded hold, the peer-close finish and
  the cleanup paths. The requester's deadline, fencing and offline-removal semantics do not
  change. `peer-revocation` and `trust-store-failure-recovery` are not modified: their rule that
  logical termination must not depend on the transport close remains true, an *existing* held link
  is still finished by an explicit revocation or reset, and a hold created during an in-flight
  removal carries no keys and is bounded.

## Impact

- **Code:** `web-extension/src/connector/sessions.ts` (hold, snapshot cleanup, connector callback),
  `web-extension/src/session/engine.ts` (the `holdForDelivery` terminal-effect flag),
  `web-extension/src/connector.ts` (the `removalHoldHandles` recovery skip and the hold-start
  wiring).
- **Tests:** `web-extension/test/session/removal.test.ts` (hold, peer close, expiry, requester,
  race, reset/revocation, cleanup), `web-extension/test/connector/connector-app.test.ts` (no
  premature disconnect and no automatic recovery), and the existing responder effect assertion and
  `web-extension/test/connector/close-peer.test.ts` (updated for the hold).
- **Docs:** `docs/otp-11-debug-controls.md` gains the follow-up device run and its evidence.
- **Systems:** none outside the extension. No schema, fixture, limit, protocol, native-host or
  Android change; no durable record shape changes.
- **Verification:** deterministic unit/connector tests on injected timers, plus a real-device run
  of at least ten Android → Chrome removals and ten Chrome → Android removals, each reporting
  `Peer removed on both devices` with both peer lists empty. The expiry fallback is unit-tested
  only unless the device exercises it; the PR note says so. If the device run cannot reproduce the
  loss before the fix, the unit test is the regression proof and the PR note records that.
