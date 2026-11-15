# Exercising peer revocation with the debug controls (OTP-11)

Steps for the three OTP-11 operations — local revoke, mutual removal and the endpoint-wide reset —
driven from the Android debug pairing screen and from the Chrome pairing debug panel. Both
surfaces exist only in debug artifacts: the Android screen and its strings live in the debug
source set (`:app:releaseHookGuard` proves they are absent from the release APK), and the Chrome
panel is a dynamic import behind the build-time development channel (`npm run test:bundle` proves
it is absent from the production bundle).

No device run of these controls has been recorded yet. The observed-values section at the end is
marked **not yet run** and stays empty until someone records values they actually saw.

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
   shows a live full session. Mutual removal needs that full session.

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
  has one, and `live session open` or `no live session`.
- Per row: `Revoke peer`, `Mutual remove` and `Clear incomplete record`.
- `Reset identity` below the destructive warning; clicking it opens the browser confirmation with
  the same warning text.
- The panel never renders an installation id.

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
   `live session open`.
2. On one endpoint, press `Mutual remove` on the peer row. This sends the removal request; the
   other endpoint deletes its record, replies and closes the session.
3. Read the outcome line on the requesting endpoint:
   - `Peer removed on both devices` — the response arrived and the local record was deleted.
   - `No open full session: only local removal is possible. Nothing was deleted.` — no full
     session; use `Revoke peer` for a local removal instead.
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
6. Confirm the peer list is empty on the reset endpoint. Android shows `No trusted peers` and,
   after a successful reset, `Trust status: Trust OK`; the Chrome panel shows the status the
   connector reports, without inventing one.
7. Re-pair from the other endpoint and record the pairing result. This verifies only that pairing
   works after a reset; success alone does not prove the reset endpoint has a new identity.

## Observed values — NOT YET RUN

No Android device or Chrome host run has been performed for OTP-11 peer revocation. Every row
below is unrun; nothing in this document is an observed result.

| Procedure | Endpoint | Result | Observed control response |
| --- | --- | --- | --- |
| Revoke while connected | Android | not run | — |
| Revoke while connected | Chrome | not run | — |
| Revoke with another peer connected | Android | not run | — |
| Clear via `REVOKE_PEER` (session open) | Android | not run | — |
| Clear via `REVOKE_PEER` (service stopped) | Android | not run | — |
| Mutual removal (live full session) | Android → Chrome | not run | — |
| Mutual removal (live full session) | Chrome → Android | not run | — |
| Mutual removal (disconnect) | either | not run | — |
| Mutual removal (response timeout) | either | not run (exercise pending) | — |
| Mutual removal (no full session) | either | not run | — |
| Reset (cancelled) | Android | not run | — |
| Reset (cancelled) | Chrome | not run | — |
| Reset (confirmed) | Android | not run | — |
| Reset (confirmed) | Chrome | not run | — |

## Limits

- Debug artifacts only: a debug APK and a development-channel extension; no release artifact links
  either control.
- The Chrome panel is mounted on the connector page in every development-channel build. Without a
  live host the connector has no coordinator: the peer list is empty, the row actions report their
  failure notice, and a reset request deletes nothing. Mutual removal additionally needs a live
  full session.
- Record only values seen on the screens: notices, peer-row states and the trust status. Never
  record installation ids, public keys, pairing codes, platform hints or transcripts.
