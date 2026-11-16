## Why

An enrollment can end with the phone still at `committed_waiting_ack` while the client already
reports `active`, because the client closes the BLE link in the same step that sends
`activation_ack`. The first reconnect is then an `activation_recovery` generation, and the protocol
keeps such a generation recovery-only for its whole life (OTP2-STA-010b step 6): no OTP data and no
mutual removal until the user disconnects and reconnects once more. The device run recorded in
`docs/otp-11-debug-controls.md` hit exactly this. Fixing it keeps the first post-enrollment session
usable and removes a manual disconnect from every pair.

## What Changes

**Enrollment teardown**
- From: the client stops the enrollment link and disconnects the transport immediately after the
  `activation_ack` frame is submitted, so the phone may never verify the ack.
- To: after sending the ack, the client holds the transport open until the phone closes it, bounded
  by a grace of about 5 s. The hold runs outside the frame queue, drops inbound frames, and keeps no
  enrollment material. A peer close finishes the link locally with no disconnect request; grace
  expiry tears down as today and leaves the enrollment to the recovery protocol.
- Reason: the phone closing the link is the only delivery evidence available, and it is already the
  phone's last act of the enrollment.
- Impact: non-breaking for the protocol; changes client teardown timing only. Erasure is unchanged:
  the enrollment keys are still wiped and the core handle still closes at the ack (OTP2-CRY-019e),
  so the hold keeps no enrollment material.

**Completed activation is terminal**
- From: `terminate()` (revocation, fail-closed, reset, port death) and the failed-send path call
  `ClientMachine#onDisconnect()`, which turns an `active` machine into a failed `idle`.
- To: once the enrollment has completed on the client, no path calls `onDisconnect()`. Teardown
  still fences, erases and closes the handle; it only skips that one call. A final send that fails
  because the phone closed first ends the same way and is left to the recovery protocol.
- Reason: the hold makes these paths reachable at a moment they never could before.
- Impact: non-breaking; removes a false failure report after a completed enrollment.

**Debug panel state readout**
- From: `pairingState.finished` and `pairingFinished` read only the link's ended flag, so a held link
  would report "not finished" for up to the grace.
- To: the finished flag also reads the machine's terminal state; `pairingFinished` keeps its meaning
  of "the attempt ended".
- Reason: the panel is the surface the device run reads the enrollment outcome from.
- Impact: debug surface only, no requirement change.

**Out of scope:** protocol, wire, fixtures and the conformance inventory; `native-bridge/` and
`android/`; the phone-side drain of inbound steps queued before `Teardown` (defense in depth for the
fallback path, recorded as a follow-up); and the sibling loss of a mutual-removal response before
the responder closes, which has its own tracked task.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `spake2-enrollment-exchange`: one requirement is ADDED, `Enrollment completion hold`, holding the
  client-side rules for ending the enrollment connection: the transport is released only after the
  phone ends it or a bounded grace expires, no inbound frame is processed while it is held, and a
  completed activation is never re-classified as a failure. No existing requirement text changes:
  `Protected enroll_identity records` keeps its "enrollment connection MUST end when activation
  completes" sentence, which the added requirement refines for the client's transport release.

## Impact

- **Code:** `web-extension/src/pairing/link.ts` (hold, timer, close notification, terminal-active
  rule), `web-extension/src/connector/controller.ts` (per-handle close notification),
  `web-extension/src/connector.ts` (finished flag), and every test double that implements
  `PairingTransport` — the new method is required on the interface, so all four update:
  `test/connector/revocation-integration.test.ts:414`, `test/connector/close-peer.test.ts:175`,
  `test/pairing/client-machine.test.ts:190` and `test/session/loopback.test.ts:756`.
- **Tests:** pairing link and connector tests advance an injected timer; the connector gains a test
  that a peer close during the hold keeps the existing invariant that an enrollment handle's
  disappearance schedules no recovery, and reports no failure; the conformance and
  enrollment-scenario suites must stay green unchanged.
- **Systems:** none outside the extension. No schema, fixture, limit or protocol change; no Android,
  host or packaging change.
- **Verification:** a real-device run (Android phone + Chrome + macOS host) of at least ten
  consecutive enrollments, each followed by its first reconnect. Per run the phone log shows the
  record `ACTIVE` before the link closes, the native-port trace shows a phone `disconnected` event
  and no client `disconnect` request, the first reconnect is a FULL session, an OTP arrives over it
  and mutual removal is accepted without an extra disconnect. Edge paths are exercised on the
  device: cancel, tab close and a revocation while the link is held. If no device test can suppress
  the phone's close, the PR note says the grace-expiry path was proven by unit tests only.
