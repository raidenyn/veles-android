## Design Summary

OTP-10 follow-up (issue #155): an enrollment can finish with the two endpoints disagreeing about the
activation. The client seals `activation_ack`, persists its record as the `active_activation_pending`
mirror, marks its machine `active`, and tears the BLE link down in the same step; the phone, which
only moves to `ACTIVE` after it verifies that ack, is left
at `committed_waiting_ack`. The first signed reconnect then runs as an `activation_recovery`
generation, and per OTP2-STA-010b step 6 such a generation stays recovery-only for its whole life
even after the phone activates during it. The pair therefore has no FULL session — no OTP data and
no mutual removal — until the user disconnects and connects once more (or the generation idles out).

The change makes the client **hold the enrollment link after sending the ack until the phone closes
it**, with a bounded fallback grace. Closing the link is the phone's own last act of the enrollment
(`Outgoing.Disconnect` after the verified ack), so the hold turns "assume the ack arrived" into
"observe that the phone finished with this connection". No protocol, wire, fixture or Android
change is needed.

## What the code does today

Observed, with the places it was observed:

- **Client, final step.** `ClientMachine#onActivationComplete`
  (`web-extension/src/pairing/client-machine.ts:748-818`) verifies `activation_complete`, persists
  the `complete` stage — the durable record becomes the `active_activation_pending` mirror
  (`web-extension/src/trust/repository.ts:111-124`) — seals the ack, erases the enrollment keys
  (OTP2-CRY-019e), sets the machine `state = 'active'` and `closed = true`, and returns the ack
  frame.
- **Client, teardown.** `PairingLink.receive` sends the returned frames, then at
  `web-extension/src/pairing/link.ts:296` stops the link because the machine is closed; `stop()`
  (`link.ts:412-422`) immediately issues `controller.disconnect(handle)`.
- **Host.** `Event::Sent` is emitted from `on_write_done`
  (`native-bridge/host/src/core.rs:1451-1462`), i.e. once every fragment was submitted, and the
  macOS backend submits with `write_without_response`
  (`native-bridge/host/src/macos.rs:676-689`). `Sent` therefore means "accepted by the OS stack",
  not "delivered to the phone". The WinRT path was not read. A `disconnect` request tears the link
  down and drops queued/in-flight work (`core.rs:1195-1210`).
- **Phone, ack handling.** `onActivationAck`
  (`android/app/src/main/java/me/nagaev/veles/pairing/PhonePairingMachine.kt:446-478`) verifies the
  ack, confirms the record, erases enrollment material, sets `PairingState.ACTIVE` and returns
  `Outgoing.Disconnect`. `SessionHost.applyPairing` (`.../sharing/SessionHost.kt:881-886`)
  executes that as teardown plus `transport.close(gen)`, and `SharingGattServer.close`
  (`.../sharing/SharingGattServer.kt:560-600`) flushes its own send queue before `cancelConnection`
  (`:757-761`). The phone is careful about its own queue.
- **Phone, second loss path.** `SessionHost.onDisconnected` sets `live.ended = true` immediately and
  queues `Teardown` (`SessionHost.kt:349-358`), while `Step.Inbound` only dispatches when
  `isCurrent(live)` (`:685-687`), which requires `!live.ended` (`:1040`). An ack that arrived and was
  queued but not yet handled when the client's disconnect lands is silently skipped. So the loss is
  not only "the OS dropped a queued write": even a fully delivered ack can be discarded because the
  client closed first. This is why a host-side drain alone cannot fix the defect.
- **Phone, bounds.** A pairing generation disconnects after `requestInactivityTimeoutSeconds = 30`
  of inactivity (`android/app/src/main/java/me/nagaev/veles/pairing/PairingLimits.kt:24`,
  `PhonePairingMachine.kt:184-190`). The phone's record is not deleted by that timeout; it stays
  `committed_waiting_ack` for the recovery protocol.
- **Client, tick.** `ClientMachine#onTick` ignores `active`
  (`client-machine.ts:397-402`), so no client-side timer can end an attempt after activation.
- **Protocol.** OTP2-STA-010b steps 0-6 and OTP2-STA-010c define the recovery exchange and the
  active-mirror record; OTP2-SID-001's upgrade case makes a reconnect whose phone confirmation
  carries `peer_state = 0x01` a FULL session immediately. OTP2-CRY-019e requires erasing the
  enrollment keys right after the ack is sent, so no retry on the same connection is possible.
- **Device evidence.** `docs/otp-11-debug-controls.md` "Observed values — run of 2026-11-15": right
  after a PASS enrollment the phone row read `Chrome · COMMITTED_WAITING_ACK` while the panel read
  `State: active · finished`, and mutual removal from that row was refused until a manual
  disconnect/reconnect produced a full session.

## Alternatives Considered

### Alternative A: Peer-close hold in the enrollment link
- **Approach**: after the ack is sent, keep the link (and only the link) until the phone closes the
  connection or a bounded grace expires; on a peer close finish locally without issuing a
  disconnect, on expiry tear down exactly as today.
- **Pros**: observes the phone's own end-of-enrollment instead of assuming delivery; removes both
  loss paths for every normal enrollment; needs no protocol, host or Android change; the phone's
  close already exists and is already the last thing it does.
- **Cons**: adds one narrow method to `PairingTransport` and an injectable timer; the link lives up
  to the grace beyond activation; the fallback path still ends with the phone at
  `committed_waiting_ack`.
- **Why not chosen**: n/a — this is the chosen approach.

### Alternative B: Fixed linger after the send resolves
- **Approach**: wait a constant, e.g. 500 ms, after `sent`, then disconnect as today.
- **Pros**: no transport API change; trivial to implement.
- **Cons**: guesses instead of observing; still loses the ack whenever the phone's worker is slow,
  and costs the constant on every enrollment; no signal to report.
- **Why not chosen**: it converts a race into a smaller race and keeps the defect that the device
  run actually hit.

### Alternative C: Host close-after-drain
- **Approach**: make the host's `disconnect` request wait until the link's send queue drains (or a
  short linger) before tearing the GATT link down.
- **Pros**: fixes the loss for every client of the transport; no per-flow logic.
- **Cons**: cannot be observed with `write_without_response`, so it still guesses; touches both the
  WinRT and CoreBluetooth backends; changes the semantics of every disconnect (revocation,
  fail-closed, reset) in a security-sensitive path.
- **Why not chosen**: it cannot fix the phone-side queued-inbound drop, which is an independent
  cause, so it would pay the widest blast radius for half the fix.

### Alternative D: Phone-side bounded ack wait (client unchanged / A plus phone drain)
- **Approach**: shorten or remove the window in which the phone can lose the ack — e.g. dispatch
  inbound steps queued before `Teardown` — with or without the client change.
- **Pros**: fixes the "fully delivered but discarded" path directly; small Kotlin change.
- **Cons**: alone it does nothing about an ack genuinely dropped in the air; as an addition to A it
  touches OTP-11's teardown ordering (`Step.Inbound` vs `Teardown`) in a fail-closed area and needs
  its own Android lint, unit and device verification.
- **Why not chosen**: A already prevents the client from disconnecting first, which is what makes
  the phone discard the ack. The drain stays a recorded follow-up with its own scope, not part of
  this change.

## Agreed Approach

Alternative A, chosen by the user after the reviewer recommended it. The mechanism, in outline —
`design.md` fixes the details:

1. The hold lives in `PairingLink`, next to the frame queue that owns the send. `ClientMachine`
   stays as it is: it is already `active`, its keys are already erased, and its tick ignores
   `active`.
2. `PairingTransport` gains one narrow close notification (a handle-level "this connection is gone"
   callback), implemented by the connector controller on top of the state transition it already
   performs when a connection leaves the snapshot.
3. The link subscribes it in `start()` — together with `onRecord`, before the first send — because
   the phone can close before the ack's `sent` resolves. A handle already gone at subscription time
   fires immediately.
4. After the ack is sent, a closed `active` machine enters a drain state instead of stopping. The
   grace timer runs from the ack's successful send, and the wait lives **outside the frame queue**:
   the queue item arms the drain and returns, so `terminate()` — which waits on that queue
   (`link.ts:375-385`) — is never delayed by the hold. Inbound frames received during the drain are
   dropped rather than fed to a confirmed-active machine, and the link keeps no secrets.
5. **Terminal-active rule.** Once the machine has completed the enrollment on the client (`closed`
   with `state === 'active'`), the link never calls `machine.onDisconnect()` again: not on a peer
   close, not on a failed final send, not from `terminate()`/`abandon()` during the drain. That call
   exists to erase a live attempt and mark failure (`client-machine.ts:387-394`); after activation
   nothing is live and there is no failure. `terminate()` keeps fencing, erasing and closing; it
   only skips that one call.
6. A peer close during the drain finishes the link locally: clear the timer, unsubscribe, notify
   ended, and issue no disconnect request (the handle is already gone).
7. A failed send of the final ack — the phone closed before `sent` resolved, which the hold makes
   more likely — ends through `stop()` as today, including its disconnect request against a
   possibly dead handle, but under the terminal-active rule the machine stays `active` instead of
   being failed. The enrollment is then left to the recovery protocol, exactly like grace expiry.
8. Grace expiry tears down exactly as today, leaving the machine `active`.
9. `cancel`, `dispose`, `terminate`/`abandon` and the in-queue abort bypass the drain and close
   immediately, and a link held in the drain is still visible to revocation's `closeAll()`. A port
   close that drops every handle must also end a draining link: the connector's existing
   `abandon()`/`abandonPairing` path is the place to confirm that, and if it does not cover it, the
   close notification fires for every open handle on a reset.
10. The debug panel's `finished` flag becomes new connector work: `pairingState`/`pairingFinished`
    (`web-extension/src/connector.ts:716-728`) currently read only `pairingEnded`, which the link's
    `onEnded` sets. They must also read the machine's terminal state so a held link does not report
    "not finished" for up to the grace; `pairingFinished`'s existing meaning (the attempt ended)
    stays for the caller that depends on it.

## Key Decisions

- **Bounded, observed hold — not a sleep.** The wait ends on the phone's close; the grace only
  bounds a lost ack.
- **Grace anchored on the ack's `sent`**, about 5 s: far above the phone's ack path (one durable trust
  write, a few erases and one disconnect) and well below the phone's 30 s inactivity timeout, so the
  client never outlives the phone's own limit. The timer is injected, and tests advance it — no
  sleeping (`.agents/rules/repo-no-time-based-tests.md`).
- **Drop inbound frames during the drain.** A phone that rejected the ack sends `preauthReject` and
  then disconnects (`PhonePairingMachine.kt:596-601`); feeding that to a confirmed-active machine
  would turn a committed record into a failed one.
- **A completed enrollment is never re-classified as a failure.** After activation the link calls
  `machine.onDisconnect()` on no path at all — peer close, failed final send, or a revocation/reset
  in the drain — and finishes without a disconnect request when the handle is already gone.
- **The hold must not slow teardown.** The wait sits outside the frame queue, so `terminate()` and
  the revocation/reset `closeAll()` path still close immediately; a held link is still selected by
  `boundPeerId()` and terminated like any other.
- **The conformance fixture is unchanged, by reading.** `close_enrollment_connection` on the client
  `activation_complete` row (`protocol/v1/states.json`) is the machine's logical close, which still
  happens; only the transport release is deferred by the hold. The web conformance scenarios observe
  that effect through `machine.closed` (`web-extension/test/pairing/enrollment-scenarios.test.ts`),
  so they keep passing and no fixture moves.
- **Scope:** `web-extension/` only. No protocol, `protocol/`, `native-bridge/`, `android/` or fixture
  change; the phone already behaves correctly.
- **Acceptance is a real-device run** (Android phone + Chrome + macOS host): at least ten consecutive
  enrollments, each followed by its first reconnect, where the phone log shows the record `ACTIVE`
  before the link closes, the native-port trace shows a phone `disconnected` event and no client
  `disconnect` request, the first reconnect is a FULL session, an OTP arrives over it, and mutual
  removal is accepted without an extra disconnect/reconnect.
- **Recorded follow-ups, not part of this change:** the phone-side inbound drain before `Teardown`
  (defense in depth for the fallback path); the sibling "last record before close" loss in mutual
  removal, which is its own tracked task.

## Open Questions

- None blocking. The WinRT `write_batch` path was not read; it does not change the design, because
  the hold does not depend on what `sent` guarantees.
- **Assumption to confirm on the device:** the phone's close reaches the client promptly. The phone
  ends the link with `BluetoothGattServer.cancelConnection`
  (`.../sharing/SharingGattServer.kt:757-761`); the client only learns of it if CoreBluetooth raises
  `didDisconnectPeripheral` (`native-bridge/host/src/macos.rs:657-668`) and the controller drops the
  handle. Android's server-side `cancelConnection` is not guaranteed to tear down a link the central
  opened, and the WinRT path was not read. If the close does not propagate, every enrollment still
  succeeds but silently takes the grace, so the device run must show the `disconnected` event
  arriving with no client `disconnect` request.
- The phone-side drain follow-up needs a tracked task before it can be planned; it is deliberately
  left out here.
- The grace-expiry path can be proven with an injected timer in unit tests; if no device test can
  suppress the phone's close, the PR note must say the fallback was unit-tested only.
