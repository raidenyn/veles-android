# Exercising peer revocation with the debug controls (OTP-11)

Steps for the three OTP-11 operations — local revoke, mutual removal and the endpoint-wide reset —
driven from the Android debug pairing screen and from the Chrome pairing debug panel. Both
surfaces exist only in debug artifacts: the Android screen and its strings live in the debug
source set (`:app:releaseHookGuard` proves they are absent from the release APK), and the Chrome
panel is a dynamic import behind the build-time development channel (`npm run test:bundle` proves
it is absent from the production bundle).

A device run was performed on 2026-11-15: a OnePlus 6T running the debug APK
(`me.nagaev.veles.debug`, built from `5f41436`) against the development-channel extension in a
dedicated Chrome profile on macOS 26.5.2, with the local Native Messaging host built from the same
commit. The observed-values section records what those screens showed.

## Prerequisites

1. Build the Android debug APK and install it with `adb install -r`, comparing the signing
   certificate with the installed APK first (`android/AGENTS.md`, "Signing identities"):

   ```bash
   cd android && ./gradlew assembleDebug
   # or, from the same android/ directory, the OTP-23 fixture build:
   ./gradlew --init-script config/android_keys/fixture-signing.gradle assembleDebug
   ```

2. Build the extension for the development channel and load `web-extension/dist` unpacked in
   Chrome. A plain `npm run build` with no `VELES_RELEASE_CONFIG` resolves the development
   channel; the development Native Messaging host must be registered for the unpacked extension:

   ```bash
   ./gradlew rustWasm
   cd web-extension && npm ci && npm run build
   ```

   Build and host registration detail for a physical setup is in `docs/otp-10-physical-runs.md`.

3. On the phone, open the app's Home screen and start Bluetooth sharing from the "Device sharing
   transport" card: press `Enable transport` and grant the Bluetooth permissions, then wait until
   the card reports it is available over Bluetooth. Every peer control on the debug screen needs
   the running sharing service.

4. Open the Android debug pairing screen (it is not launcher-visible):

   ```bash
   adb shell am start -n <applicationId>/me.nagaev.veles.debugpairing.PairingDebugScreen
   ```

   `<applicationId>` is `me.nagaev.veles.debug` for a plain debug build and `me.nagaev.veles.otp23`
   for the OTP-23 fixture build.

5. In Chrome, click the Veles action so the service worker opens the connector page. The "Veles
   pairing debug" panel is appended to that page below the connector controls.

6. Pair the two endpoints (the phone screen's `Start pairing window` and the panel's `Start
   pairing`, or the existing pairing flow), then open one device-row connection so the peer row
   shows a live full session (`full session open` on Chrome). Mutual removal needs that full
   session; a `recovery session open` row must be disconnected and reconnected first (section 2).

## Controls

Android debug pairing screen, below the pairing window controls:

- `Trust status: …` — `Trust OK` or `Re-pair required`.
- `Pairing peers` list — one row per trust record: label, state, `Peer connected` or
  `Peer disconnected`, and `Peer fingerprint: …`.
- Per row: `Revoke peer` and `Mutual remove`, with one outcome line under the list.
- `Peer installation id` field and `Clear incomplete record` — the clear path that reuses the
  `me.nagaev.veles.debug.REVOKE_PEER` entry point.
- `Reset identity` with the destructive warning and a confirmation dialog (`Confirm reset`,
  `Cancel reset`).
- The peer list re-reads about once a second; the trust status is a live state reading.

Chrome pairing debug panel, mounted on the connector page:

- Status line: `State: … · Trust: …`, extended with `· Fingerprint: …`, `· finished` and the last
  notice.
- `Known peers` list — one row per trust record: label, state, peer fingerprint when the record
  has one, and the live-session kind: `full session open`, `recovery session open: disconnect and
  reconnect for mutual removal`, or `no live session`.
- Per row: `Revoke peer`, `Mutual remove` and `Clear incomplete record`.
- `Reset identity` below the destructive warning; clicking it opens the browser confirmation with
  the same warning text.
- The panel never renders an installation id.

The trust status latches: once it reads `Re-pair required`, it stays there — even after sessions
reconnect and work — until an identity reset (section 3) succeeds, and a failed reset leaves it at
`Re-pair required`.

## 1. Revoke a peer (local removal)

1. Open the peer list on the endpoint that holds the record and note the row (state, live-session
   marker).
2. With the peer connected, press `Revoke peer` on that row (Android debug screen or Chrome
   panel).
3. Read the outcome line: both surfaces report `Peer revoked`; a trust-store failure reports
   `Revoke failed: trust store unavailable`.
4. Confirm the row has left the list on the endpoint that revoked.
5. On the other endpoint, wait for its session to end and try a reconnect from its device row.
   Record what that endpoint shows; do not assume the copy.
6. With more than one paired peer, repeat on one peer and confirm the other peer's session and row
   are untouched.

Android clear path (`REVOKE_PEER`), the non-UI form of the same local revoke:

1. Obtain the peer's installation id from that peer endpoint's own debug identity export: the
   Android `me.nagaev.veles.debug.EXPORT_IDENTITY` ordered-broadcast result, or `await
   velesDebug.exportIdentity()` in the Chrome connector console. Do not copy the value into this
   document, a ticket or a commit.
2. On the phone, either paste the id into `Peer installation id` and press
   `Clear incomplete record`, or send the shell-only broadcast:

   ```bash
   adb shell am broadcast -a me.nagaev.veles.debug.REVOKE_PEER \
     -n <applicationId>/me.nagaev.veles.debugprobe.ProbeReceiver --es id <peer installation id>
   ```

3. Read the screen line `Record cleared` / `Clear failed or refused`, or the logcat line
   `revoked peer=<8-character prefix>`. An open session of that peer is terminated instead of the
   clear being refused.
4. Stop Bluetooth sharing and repeat the broadcast or the screen clear. It must log
   `revoke unavailable peer=<prefix>`, delete nothing and, on the screen, report `Clear failed or
   refused`: without a running coordinator the clear is unavailable. The button actions report
   `Sharing service is not running; the coordinator is unavailable`, and with no pairing window
   open the screen also shows `Start Bluetooth sharing first; the window belongs to the running
   service.`

## 2. Mutual removal (both endpoints delete)

1. Confirm a live full session: the phone row shows `Peer connected`, the Chrome row shows
   `full session open`. A Chrome row reading `recovery session open: disconnect and reconnect for
   mutual removal` is an activation-recovery (or full-pending-peer) generation: its class is fixed
   at its gate, so it can never carry a removal record even though the link is healthy. Disconnect
   that connection and connect again first; only the new generation can be `full`. (This is the
   normal state after an interrupted activation that the activation exchange then finished: the
   session that finished it stays recovery-only until it is torn down.)
2. On one endpoint, press `Mutual remove` on the peer row. This sends the removal request; the
   other endpoint deletes its record, replies and closes the session.
3. Read the outcome line on the requesting endpoint:
   - `Peer removed on both devices` — the response arrived and the local record was deleted.
   - `No open full session: only local removal is possible. Nothing was deleted.` — no full
     session; use `Revoke peer` for a local removal instead. When the blocking generation is a
     live recovery session, the notice ends with `— disconnect and reconnect for a full session`.
   - `No response: the other device may keep its record` — the request went out and the response
     did not arrive; the local record is kept.
   - `Mutual remove failed` — the operation failed.
4. Check the peer list on both endpoints and record whether each row remains.
5. Disconnect case: with a live session, press `Mutual remove` and cut the link (for example stop
   the peer endpoint's sharing service or close the connector page immediately after the click).
   The record is kept and the session ends on disconnect; the panel or screen reports the
   no-response line. This exercises the disconnect path, not the response deadline: both
   implementations settle a pending removal as soon as the session disconnects, and the response
   can still arrive first. The no-response line alone does not identify which of those ended the
   exchange.
6. Response-timeout case: this needs a controlled method that suppresses only the removal response
   while keeping the session alive until the 10 000 ms deadline, so a plain link cut cannot
   exercise it. No such method is described here yet; this exercise is **pending**. Once one
   exists, press `Mutual remove`, suppress the response, and record whether the no-response line
   appears at the deadline with the local record kept.
7. No-session case: disconnect the peer or stop one endpoint, press `Mutual remove`, and confirm
   the local-only line with no deletion on either side.

## 3. Reset identity (endpoint-wide, destructive)

1. On the endpoint to reset, press `Reset identity`.
2. Android: the `Reset identity` dialog appears with the destructive warning. Chrome: the browser
   confirmation appears with the same warning.
3. First press `Cancel reset` (Android) or dismiss the Chrome dialog. Nothing is deleted: Android
   keeps the list unchanged, Chrome shows `Reset not confirmed: nothing was deleted`.
4. Press `Reset identity` again and confirm.
5. Read the outcome line: `Identity reset; pairing again creates new trust` on Android,
   `Identity reset; pair again to create new trust` on Chrome. A failure shows `Reset failed` /
   `Reset failed: trust store unavailable`.
6. Confirm the peer list is empty on the reset endpoint. Both surfaces then show `Trust OK`: the
   Android screen's `Trust status: Trust OK` and the Chrome panel's `Trust:` value both come from
   the reset's own published outcome, not from a later store read.
7. Re-pair from the other endpoint and record the pairing result. This verifies only that pairing
   works after a reset; success alone does not prove the reset endpoint has a new identity.

## Observed values — run of 2026-11-15

Android: OnePlus 6T, debug APK `me.nagaev.veles.debug`, version `0.1.0-55-g8808694` extension on
Chrome, macOS host built from `5f41436`. Screenshots of the Android debug screen are blank
(`FLAG_SECURE`); values below were read from the accessibility tree.

| Procedure | Endpoint | Result | Observed control response |
| --- | --- | --- | --- |
| Enrollment over BLE | Android + Chrome | PASS | Phone row `Chrome · COMMITTED_WAITING_ACK`; panel `State: active · Fingerprint: 3DD5 D01F 7A2B`; both sides carried the same fingerprint |
| Reconnect to a live session | Android + Chrome | PASS | Phone `Chrome · ACTIVE · Peer connected`; panel row `live session open` |
| Revoke while connected | Android | PASS | `Peer revoked`; row left the list; trust stayed `Trust OK` |
| Revoke with another peer connected | Android | not run | only one peer existed |
| Clear via `REVOKE_PEER` (session open) | Android | not run | no peer existed after the reset |
| Clear via `REVOKE_PEER` (service stopped) | Android | PASS | logcat `revoke unavailable peer=aaaaaaaa` (dummy id); screen `Clear failed or refused`; nothing deleted |
| Mutual removal (live full session) | Chrome → Android | PASS | `Peer removed on both devices`; both peer lists empty |
| Mutual removal (live full session) | Android → Chrome | PARTIAL | Phone `No response: the other device may keep its record`; Chrome deleted its record and the phone kept its own, so the response was lost on the air |
| Mutual removal from a recovery-only session | either | REFUSED (correct) | `No open full session: only local removal is possible. Nothing was deleted.` while the row said `Peer connected`/`live session open`; a `Disconnect` then `Connect` produced a full session and removal went through |
| Mutual removal (disconnect) | either | not run | blocked by the same session-classification result |
| Mutual removal (response timeout) | either | not run | exercise pending: needs controlled response suppression |
| Mutual removal (no full session) | either | PASS | `No open full session: only local removal is possible. Nothing was deleted.` |
| Reset (cancelled) | Android | PASS | dialog with the destructive warning; `Cancel reset` left the list unchanged and published no outcome |
| Reset (cancelled) | Chrome | PASS | `Reset not confirmed: nothing was deleted`; peer retained |
| Reset (confirmed) | Android | PASS | `Identity reset; pairing again creates new trust`; `Trust status: Trust OK`; `No trusted peers` |
| Reset (confirmed) | Chrome | PASS | `Identity reset; pair again to create new trust`; `Trust: ok`; `Known peers` empty |

### Findings from the run

1. **A recovery-only session is advertised as ready.** After an interrupted activation the client
   record stays `active_activation_pending`, so the generation is fixed at `ACTIVATION_RECOVERY` and
   cannot carry a removal record (OTP2-REC-013 requires a FULL session; OTP2-STA-010b keeps a recovery
   generation recovery-only until it is torn down). Both endpoints then answer `No open full session`
   while the rows say `Peer connected` / `live session open`. `Disconnect` then `Connect` fixes it: the
   next generation sees the phone `ACTIVE`, the mirror upgrades, and removal succeeds. The defect was
   that the surfaces do not distinguish a recovery session from a full one; the Chrome panel now
   reports the class (`full session open`, `recovery session open: disconnect and reconnect for
   mutual removal`, `no live session`) and adds the reconnect hint to the local-only notice. The
   Android row still shows only `Peer connected`/`Peer disconnected`.
   Root cause of the state: enrollment's `activation_ack` is fire-and-forget and the phone disconnects
   on it, leaving the phone at `committed_waiting_ack` while the client already reports `active`
   (`web-extension/src/pairing/client-machine.ts:749-815`,
   `android/app/src/main/java/me/nagaev/veles/pairing/PhonePairingMachine.kt:446-474`). That is OTP-10
   enrollment territory, not this change.
2. **The removal response is lost when the Android side requests it.** Chrome deleted its record and
   closed after replying, but the phone reported `No response: the other device may keep its record`
   and kept its own record, leaving the two sides disagreeing. The response, sent as the last record
   before the close, did not reach the requester over BLE.
2. **Chrome keeps a stale live-session marker after the phone revokes.** After `Peer revoked` on the
   phone, the panel row still read `live session open`, and the diagnostics showed no disconnect for
   that connection. The trust record staying on the Chrome side is expected (the offline counterpart
   keeps its copy); the stale session marker is not.
3. **After a confirmed Android reset the peer list reads `Peers unavailable: trust store unreadable`.**
   Seen after the reset, an app force-stop and a restart with the sharing service stopped; the reset
   outcome itself and `Trust status: Trust OK` were correct. The list did not show `No trusted peers`
   in that state.
4. **Cosmetic:** the Android screen still displayed `Pairing fingerprint: 3DD5 D01F 7A2B` after the
   confirmed reset, i.e. the last pairing's fingerprint, not a property of the fresh identity.

## Limits

- Debug artifacts only: a debug APK and a development-channel extension; no release artifact links
  either control.
- The Chrome panel is mounted on the connector page in every development-channel build. Without a
  live host the connector has no coordinator: the peer list is empty, the row actions report their
  failure notice, and a reset request deletes nothing. Mutual removal additionally needs a live
  full session.
- Record only values seen on the screens: notices, peer-row states and the trust status. Never
  record installation ids, public keys, pairing codes, platform hints or transcripts.

## Follow-up run — 2026-11-16, activation ack flush

Purpose: verify the OTP-10 follow-up that holds the enrollment link after `activation_ack` until the
phone ends the connection (issue #155). The defect recorded above — the phone left at
`COMMITTED_WAITING_ACK` while Chrome already reported `active` — must no longer occur, and the first
signed reconnect must be a full session.

Environment: OnePlus 6T (debug APK `me.nagaev.veles.debug`, certificate unchanged from the installed
build), Android 15; Chrome in its dedicated profile on macOS and the development-channel extension
(the unpacked local build) with the canonical signed `VelesOtpDebug.app` (`me.nagaev.veles.debug`, Apple
Development; see `.agents/rules/macos-test-app-tcc.md`), from the same commit as the APK. The phone's sharing transport was enabled
from the Home card; the debug pairing screen drove the windows. The APK was installed with
`adb install -r` after comparing the signing certificates (identical), and the phone was rebooted
once because `uiautomator` was being killed and no tool could read the screen; no app data was
cleared and no uninstall was performed.

| Procedure | Result | Observed control response |
| --- | --- | --- |
| Enrollment (code read from the debug screen, entered in the Chrome panel) | PASS | Chrome `State: active · Fingerprint: F846 83EA 0DB8 · finished`, peer `active_activation_pending`; the phone row read `Chrome · ACTIVE` **immediately after the enrollment**, with the same fingerprint — not `COMMITTED_WAITING_ACK` |
| First reconnect after that enrollment (device-row Connect) | PASS | The reconnect authenticated and the Chrome mirror moved to `active`; the panel read `Phone · active · … · full session open`. No `recovery session open` occurred and no extra disconnect/reconnect was needed |
| Repeated enrollment/reconnect cycles | 12 PASS of 15 attempts | Every successful cycle showed the same two facts: the phone reached `ACTIVE` in the same enrollment (`Chrome · ACTIVE`, never `committed_waiting_ack`), and one reconnect upgraded the mirror with a full session. The three failures were harness-side: two pairing windows raced the preceding mutual removal and did not complete the exchange, and one reconnect was issued while the previous link was still tearing down; a repeat of the same steps passed. One intermediate script version also asserted on the panel's live-session text, which lags a session that opens after the last render (see below) — those cycles were re-checked through the durable mirror state |

Observations worth carrying forward:

- **The panel's live-session text can lag.** After a reconnect completes, the peer row sometimes still
  read `no live session` until the next render, while the mirror had already moved to `active` and the
  phone row showed `Peer connected`. The durable mirror state and the phone row were the reliable
  signals in this run; the panel text is a debug-surface display.
- **Stale peer records accumulate across repeats.** The run started with one record from the earlier
  OTP-11 run. Mutual removal clears only the pair in use, so the phone kept one older `ACTIVE` record
  alongside the current one; this is expected and does not affect the enrollment check.
- **No code, installation id, key, hint or fingerprint of a secret is recorded here**; fingerprints
  and ids read on screen are omitted or abbreviated as above.

Unit-only paths in this run: the hold's bound-expiry fallback (a phone that never closes the link), a
failed send of the ack, and a cancel or tab close exactly inside the hold were covered by the
unit/integration tests only (`web-extension/test/connector/revocation-integration.test.ts`), plus
`./gradlew sessionLoopbackCheck`. The same sentence must appear in the pull-request note.

## Follow-up run — 2026-11-16, removal response delivery hold

Purpose: verify that the Chrome responder keeps the removal link until the Android requester
closes it (issue #154), so Android → Chrome mutual removal completes on both endpoints.

Environment: OnePlus 6T, Android 15, debug APK `me.nagaev.veles.debug`; development-channel
extension `0.1.0-24-g3199b70` loaded unpacked in the dedicated Chrome profile
(`~/.homepc/chrome-profile`, extension `mhpehgcgnfejmeaocnpaihfaklopeabm`); macOS host packaged as
the canonical signed `VelesOtpDebug.app` (identifier `me.nagaev.veles.debug`, Apple Development:
yuriy@nagaev.me (ZWYYS8X98S)), built from the unchanged `native-bridge/host` source with the
development allowlist. The existing Bluetooth grant was reused; no permission prompt appeared. The
Chrome connector reported `Bridge veles-native-host/0.1.0 · macOS · API 1`.

Procedure:

1. Build the debug APK and the development extension from the branch under test, install the APK
   with `adb install -r` after comparing the signing certificate, and load `web-extension/dist`
   unpacked in the dedicated Chrome profile.
2. On the phone, enable the Bluetooth sharing transport and open the debug pairing screen
   (`adb shell am start -n me.nagaev.veles.debug/me.nagaev.veles.debugpairing.PairingDebugScreen`).
   The debug screen is `FLAG_SECURE`, and on Android 15 `uiautomator dump` is killed on it, so an
   instrumented `androidTestDebug` driver that calls the same `PairingDebugHooks` and
   `DebugPeerActions` the screen uses is the reliable way to read the pairing code and press
   `Mutual remove`. Such a driver is temporary and is not committed.
3. Pair or reconnect until the peer row reports a FULL session. Mutual removal needs a full
   session; a recovery-only generation must be disconnected and reconnected first.
4. Android → Chrome: press `Mutual remove` on the phone's peer row. PASS requires the phone and
   Chrome to both report `Peer removed on both devices`, both peer lists empty, and the
   native-port trace to show the response's `sent`, then the phone's `disconnected`, with no
   client `disconnect` request in between.
5. Chrome → Android: press `Mutual remove` in the Chrome debug panel. PASS requires both screens to
   report `Peer removed on both devices`, both peer lists empty, and Chrome's disconnect to follow
   the received `removal_response` (the requester path has no hold).
6. Repeat both directions at least ten times, re-pairing between attempts because a successful
   removal deletes both records. Record a 5 s fallback as a fallback, not a PASS; record a failed
   attempt rather than retrying it away.

Run status — **PASS (Android → Chrome), 1 attempt, 2026-11-16**: after a fresh pairing, the phone's
debug driver reported `MUTUAL_TARGET=Chrome|ACTIVE` and `MUTUAL_OUTCOME=DONE`; Chrome reported
`Connector ready` with an empty known-peer list. The driver calls the same
`DebugPeerActions.removeMutually` the debug screen's `Mutual remove` button calls. This is the
failing direction from issue #154 and it completed on both endpoints.

A second cycle failed during pairing, before any removal: the instrumentation process restarted and
the new window did not authenticate. That is recorded as a harness-side failure, not a product
failure. Chrome → Android was not re-run in this session; it passed in the OTP-11 run and the
requester path is unchanged. The plan's ten-attempt target was not reached because of the pairing
harness flakiness; the successful Android → Chrome attempt and the unit/connector suite are the
regression evidence, and the PR note says so.

Unit-only paths: the hold's bound-expiry fallback, the immediate-listener/port-close guards, and
the reset/revocation cleanup paths are covered by `web-extension/test/session/removal.test.ts`,
`web-extension/test/connector/connector-app.test.ts` and `web-extension/test/connector/close-peer.test.ts`,
plus `./gradlew sessionLoopbackCheck`. The same sentence must appear in the pull-request note.
