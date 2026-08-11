# Popup-owned Web Bluetooth feasibility spike

## Status

Approved on 2026-08-11.

This specification defines the implementation and real-hardware validation for GitHub issue
#77. It is the feasibility gate for the popup-owned Bluetooth architecture in the approved
Android-Chrome Bluetooth OTP sharing roadmap.

## Goal

Determine whether a Manifest V3 extension popup can own a useful authenticated Web Bluetooth
session with an Android BLE peripheral on stable Chrome for Windows and macOS.

The spike must prove or disprove these assumptions with synthetic OTP data:

- The popup can select, pair with, and connect to an Android BLE peripheral.
- Required chooser and operating-system pairing interactions do not make the popup unusable.
- An authenticated session can pull current data and receive pushed data while the popup is
  open.
- An asynchronous Bluetooth event can write the synthetic OTP code to the clipboard.
- Closing the popup ends its useful session, and reopening it requires explicit device
  selection again.
- One phone can serve Windows and macOS computers concurrently.
- One popup can maintain sessions with two Android phones concurrently.
- Android advertising and connected sessions remain available through the required
  foreground-service lifecycle.

The outcome is a written, reproducible go/no-go decision. It does not establish production
Bluetooth, protocol, extension, or user-interface foundations.

## Environment separation

Implementation and physical testing occur in separate environments.

The implementation environment may compile the Android app, run automated tests, inspect the
extension, and create the pull request. It has no access to the required physical matrix and
must not claim that Bluetooth feasibility has passed. Every physical result starts as
`Not run`.

The testing environments are the user's physical Windows and macOS computers and Android
phones. The user checks out the pull request commit, builds and installs the debug APK, loads
the unpacked extension in stable Chrome, executes the written matrix, and returns the observed
results. The exact commit tested is part of every result set.

## Scope boundaries

### Included

- A debug-only Android BLE peripheral and connected-device foreground-service harness.
- A dependency-free unpacked Manifest V3 popup extension.
- Encrypted GATT access to exercise any required operating-system pairing flow.
- A non-production challenge-response session using synthetic shared key material.
- Synthetic current-data pull, pushed events, asynchronous copy, and multi-client behavior.
- Explicit status and timestamped event output for manual diagnosis.
- Focused automated tests for transport-independent Android logic.
- Build, installation, reset, Chrome loading, and physical-test instructions.
- A written validation report and final architecture decision.

### Excluded

- Existing notification-listener or `OtpMessage` integration.
- Real OTP, bank, merchant, account, or clipboard data.
- Production PAKE, key storage, identity, trust, encryption, framing, or compatibility choices.
- Durable history, pairing records, settings, or client management.
- A TypeScript or npm project and Gradle integration for the extension.
- Production Android UI or release-manifest changes.
- Extension service workers, side panels, tabs, offscreen documents, or experimental device
  recovery APIs.
- Chrome Web Store packaging and release UX.

## Repository boundaries

### Android

All Android harness code and resources live under `app/src/debug`. A debug-only manifest adds
the Bluetooth permissions, spike launcher activity, and connected-device foreground service.
The release source set does not reference the harness.

This uses the existing `:app` package and build configuration so the lifecycle test exercises
Veles rather than a neighboring sample application. `assembleRelease` must prove that no spike
component or permission leaks into the release APK.

### Chrome

The unpacked extension lives at `spikes/web-bluetooth-popup/`. It consists only of a Manifest
V3 manifest and static HTML, CSS, and JavaScript files that Chrome can load directly from the
checked-out directory.

It has no package manager, transpiler, bundler, Gradle task, service worker, or offscreen
document. Those foundations belong to later roadmap issues if this spike passes.

## Android components

### Spike activity

The debug-only launcher activity provides a deliberately utilitarian test surface. It:

- Requests the required Nearby Devices permissions.
- Starts and stops the spike foreground service through explicit user actions.
- Shows Bluetooth capability, permission, service, advertising, and GATT status.
- Lists connected clients and whether each client is subscribed and authenticated.
- Triggers uniquely numbered synthetic OTP pushes.
- Schedules a synthetic push after a selected delay, including a 20-minute lifecycle preset.
- Shows a bounded timestamped event log without production notification data.

The activity does not modify the existing Compose navigation or production Home screen.

### Spike foreground service

The debug-only connected-device foreground service owns:

- BLE capability checks and advertising.
- The GATT server and fixed spike service definition.
- Per-Bluetooth-device connection, subscription, authentication, and heartbeat state.
- Message chunk reassembly and serialized outbound notifications.
- Current-data responses and independent push fan-out.
- Cleanup when a device disconnects, authentication expires, a heartbeat times out, or the
  service stops.

The service starts only from the visible spike activity. Its ongoing notification reports
that the BLE spike is active and opens the activity. Stopping it closes the GATT server,
cancels advertising, disconnects clients, clears all session state, and cancels scheduled
synthetic events.

The lifecycle gate requires continued availability after the app is backgrounded, its task is
removed, and the phone is locked with its screen off for 15 minutes. Process death, reboot,
Android Task Manager Stop, force-stop, and OEM-specific restoration are not part of this
spike.

## Extension popup

The extension action opens the only Bluetooth-owning document. The popup:

- Calls `navigator.bluetooth.requestDevice()` directly from a Connect button user gesture.
- Filters the chooser to the fixed spike service UUID.
- Allows the Connect action to be repeated for a second phone without dropping the first.
- Keeps each `BluetoothDevice`, GATT characteristic, operation queue, and session state
  independent.
- Shows per-phone connection, subscription, authentication, pull, push, and error status.
- Displays synthetic current and pushed OTP values with their source phone and event number.
- Supports manual copy and a test-only `Copy next push` action.
- Shows a bounded timestamped event log for the current popup lifetime.
- Keeps no session, device, OTP, or event-log state after the popup closes.

The extension declares only the permissions needed for the action popup and asynchronous
clipboard write. It does not use background execution or persistent storage.

## GATT and session flow

### GATT surface

The phone advertises one fixed custom service. The service has:

- A command characteristic written by Chrome with response.
- An event characteristic used for notifications from Android.
- A Client Characteristic Configuration descriptor used to subscribe.

Protected characteristic operations use encrypted GATT permissions. The test records whether
each operating system requires prior Bluetooth pairing, initiates pairing during the protected
operation, or applies another supported sequence. A platform passes when its required sequence
can be completed and the popup remains usable through chooser and pairing interactions.

### Framing

Spike messages are UTF-8 envelopes divided into small indexed binary chunks and reassembled
with strict message-size, chunk-count, duplicate, and timeout bounds. Writes are serialized per
phone because platform GATT implementations may reject parallel operations.

This framing exists only to prevent negotiated MTU differences from obscuring the popup
lifecycle result. It is not the production protocol framing design.

### Authentication

Both implementations contain the same conspicuously labeled synthetic test key. It is public
repository data and must never be described as secure production key material.

Authentication uses fresh client and server nonces, a session identifier, and mutual
HMAC-SHA-256 proofs with role-separated transcript labels:

1. Chrome subscribes and sends a client nonce.
2. Android creates a server nonce and session identifier and returns a server proof.
3. Chrome verifies the server proof and returns a client proof.
4. Android verifies the client proof and marks only that connection authenticated.
5. Android returns authentication success; requests made before success receive no synthetic
   OTP data.

Authentication attempts and incomplete fragmented messages expire. Proofs from an old session
must not authenticate a new session. This validates application-level gating without choosing
the production PAKE or session protocol.

### Pull and push

After authentication, Chrome can request a fixed synthetic current OTP envelope containing an
event number, six-digit code, merchant label, amount, and currency. Android returns it only to
the requesting client.

The activity can generate a new synthetic push with a unique event number. The service sends
it independently to every subscribed authenticated client. A failed or slow client must not
block another client. Events are not queued for disconnected clients and are not persisted.

### Asynchronous clipboard test

Before triggering a push, the tester selects `Copy next push` in the popup. The Bluetooth
notification callback calls `navigator.clipboard.writeText()` with the synthetic code using
the extension's `clipboardWrite` permission. The popup reports the resolved value or exact
failure. This tests clipboard access outside the Connect user gesture rather than merely
testing a copy button.

### Popup closure

Each authenticated session sends a short heartbeat. Closing the popup stops heartbeats and
destroys all JavaScript Bluetooth objects. Android records the platform disconnect callback
or expires and cancels the session after the bounded heartbeat timeout.

Reopening the popup starts with no live connection state. Every phone requires another
Connect action and explicit selection through `requestDevice()`, even if Chrome or the
operating system remembers prior permission or pairing.

## Failure handling

Android reports these states separately:

- BLE peripheral advertising unsupported.
- Bluetooth disabled or required permission denied.
- Foreground-service, advertiser, or GATT-server startup failure.
- Connection, subscription, pairing, or authentication failure.
- Malformed, duplicated, oversized, incomplete, or expired message framing.
- Write or notification failure and client timeout.

The popup reports chooser cancellation, unavailable Web Bluetooth, GATT connection and
discovery failures, subscription failure, authentication rejection, malformed response,
request timeout, disconnect, and clipboard denial. A per-phone failure does not close another
phone's connection.

The spike must not log real notification content, production OTPs, the synthetic shared key,
or HMAC intermediate values. Synthetic event identifiers and values may appear in the visible
test logs and written report.

## Automated verification

Debug JVM tests cover the transport-independent behavior:

- Chunk splitting, ordered reassembly, bounds, duplicates, and expiry.
- Matching and rejected HMAC proofs.
- Expired or replayed authentication transcripts.
- Rejection of data requests before authentication.
- Independent client session removal and fan-out.

Before opening the pull request, the implementation environment runs:

```bash
./gradlew testDebugUnitTest
./gradlew assembleDebug
./gradlew assembleRelease
```

The release APK is inspected to confirm that no spike activity, service, Bluetooth permission,
or debug package is present. The unpacked extension is checked for manifest and popup console
errors, but only the physical matrix can validate Web Bluetooth.

## Physical validation matrix

### Environment record

The written report records:

- Pull-request number and exact commit SHA.
- Android phone model, Android version/build, and app debug version for each phone.
- Windows computer model, Windows version/build, Bluetooth adapter details when available, and
  stable Chrome version.
- Mac model, macOS version/build, Bluetooth adapter details when available, and stable Chrome
  version.
- Any OS-level pairing prerequisites, prompts, permissions, or settings.

### Cases

1. **Windows single session:** From cleared Bluetooth pairing and extension state, select the
   phone, complete the required pairing sequence, authenticate, pull current data, receive a
   pushed event, and copy that push asynchronously.
2. **macOS single session:** Repeat the fresh-state sequence on stable Chrome for macOS.
3. **Windows popup closure:** Close an authenticated popup, record Android disconnect or timeout,
   reopen it, confirm there is no live session, explicitly select the phone again, and pull.
4. **macOS popup closure:** Repeat the closure and reselection case on macOS.
5. **One phone, two computers:** Keep Windows and macOS popups authenticated to the same phone;
   pull independently and verify that one push reaches both without cross-client blocking.
6. **Two phones, one Windows popup:** Connect and authenticate two phones from one popup and
   verify source-specific pulls and pushes while both connections remain active.
7. **Two phones, one macOS popup:** Repeat the two-phone topology on macOS.
8. **Android foreground lifecycle:** Start the service and schedule a push for 20 minutes,
   background and remove the app task, and lock the phone with its screen off. After 15 minutes,
   confirm the foreground indication remains, connect a new popup from an already paired
   computer, pull current data, and remain connected until the scheduled push arrives.

Fresh pairing runs once per desktop platform. Popup reconnection and both multi-device
topologies run twice from a clean popup lifetime to expose instability. Every case records its
steps, expected result, actual result, pass/fail state, relevant timing, and limitations.

### Decision rule

The decision is **go** only when every core case passes on stable Chrome for both supported
desktop operating systems. Prompt wording differences and a measured bounded disconnect delay
may be recorded as non-blocking limitations.

A repeatable failure of chooser/pairing survival, authenticated pull/push, asynchronous copy,
explicit reselection, either required multi-device topology, or the foreground-service
lifecycle produces **no-go**. The report then recommends returning the roadmap to a persistent
side-panel or tab design.

A harness defect is fixed and retested rather than counted as a platform failure. A result
blocked by unavailable hardware, an unidentified environment problem, or an unresolved harness
problem is **inconclusive**, never go. Experimental offscreen APIs are not an automatic or tested
fallback.

## Pull-request and tester workflow

### Initial implementation PR

After local automated verification, the implementation is committed, pushed, and opened as a
draft pull request. The PR includes the harness, instructions, and validation report with all
physical cases marked `Not run`. Its description states that CI validates construction only
and that the issue outcome is waiting for external physical testing.

### Tester checkout and Android setup

The harness README gives commands for both GitHub CLI and normal Git checkout of the PR, then
identifies the tested commit SHA. It documents the required JDK and Android SDK setup and uses:

```bash
./gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

It explains how to launch the debug-only spike activity, grant Nearby Devices permission,
start and stop the foreground service, schedule a push, and clear Android Bluetooth pairing
and app data before clean-state cases.

### Chrome extension installation

The README gives these stable Chrome steps on both desktop platforms:

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Select **Load unpacked**.
4. Select the checked-out `spikes/web-bluetooth-popup/` directory.
5. Pin the Veles Web Bluetooth Spike action to the toolbar.
6. Open the action popup and use **Connect phone** for each desired phone.

It also explains how to keep the popup inspector open while diagnosing errors, reload the
extension after pulling a newer PR commit, remove and reload the extension when a clean
extension installation is required, clear relevant Chrome Bluetooth grants, and remove OS
Bluetooth pairing before fresh-pairing cases.

### Results and iteration

The user returns the completed written observations from the physical machines. The
implementation environment updates the report in the same PR. If the harness changes, the PR
records a new commit and the user pulls that commit and reruns every affected case.

The PR remains draft until the report has enough physical evidence for a go/no-go decision.
After the report and decision are committed, the PR is marked ready for review. It is not
merged solely because automated checks pass.

## Deliverables

- Debug-only Android BLE harness under `app/src/debug`.
- Dependency-free MV3 popup under `spikes/web-bluetooth-popup/`.
- Harness and cross-machine testing README, including Chrome installation instructions.
- `docs/spikes/2026-08-11-popup-web-bluetooth-validation.md` with the complete test matrix,
  observations, limitations, and decision.
- Draft implementation PR that remains open through external physical validation.
- Final GitHub issue update linking the report and stating go, no-go, or inconclusive.

The spike is complete only after the report contains the physical results and explicit
architecture decision. Implementation completion alone is only the handoff point to testing.
