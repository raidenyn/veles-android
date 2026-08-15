# Android-Chrome Bluetooth OTP sharing roadmap

## Status

Provisionally revised on 2026-08-15 from the partial physical results in PR #78.

This is the current normative roadmap. It replaces the original action-popup,
OS-pairing, and encrypted-GATT assumptions. The implementation backlog and
GitHub project are intentionally not final until Issue #77 completes its
remaining physical transport gates.

The application security protocol is defined by
`2026-08-14-bluetooth-application-security-design.md`. No real OTP may cross
Bluetooth until that protocol and its release gates are implemented and
accepted.

## Confirmed decisions

- A Chrome action popup cannot own the required `requestDevice()` flow on
  desktop Chrome. The toolbar action opens or focuses a dedicated extension
  connector tab.
- The connector tab is the sole owner of Web Bluetooth objects and live GATT
  sessions. Closing it ends those sessions.
- The connector tab may remain open in the background while the user browses
  in another tab. Background delivery is a required behavior, not a best-effort
  enhancement.
- Chrome must still show its device chooser when a new connector-tab lifetime
  selects a phone. Stable behavior does not depend on automatic device recovery.
- Windows Chrome cannot reliably use GATT attributes protected by authenticated
  BLE pairing. The cross-platform transport therefore uses plain GATT.
- OS Bluetooth pairing is not a setup prerequisite or a trust signal. A stale
  Windows bond is a troubleshooting condition, not part of normal onboarding.
- All peer trust, confidentiality, integrity, and replay protection are supplied
  by the Veles application protocol.
- Android sensitive-notification companion association is a separate
  permission-only flow. It is never Chrome trust, transport setup, or identity.
- No native desktop application is planned.

## Goal

Let a user explicitly connect Veles Chrome to Veles Android over local
Bluetooth, retrieve recent OTP transaction data, and receive new OTPs while a
connector tab remains open. The connector can be backgrounded while the user
works in another browser tab. Chrome displays the code, merchant, and amount
and supports manual or opt-in automatic copying.

The feature introduces no internet transport, cloud service, account,
telemetry, native desktop process, advertisement, or payment.

## Non-goals

- Native Windows, macOS, or Linux companion software.
- Cloud relay, internet synchronization, or user accounts.
- Durable OTP history on Android or Chrome.
- Trust based on Bluetooth address, Bluetooth name, OS bond, or companion
  association.
- Hiding Bluetooth traffic timing, service UUIDs, or encrypted record sizes.
- Protecting OTPs after compromise of either endpoint, the browser profile,
  operating system, or clipboard.
- Stable background Bluetooth ownership through experimental offscreen APIs.

## Spike evidence

### Confirmed findings

Physical testing in PR #78 established:

- An MV3 action popup rejects `requestDevice()` without presenting the chooser.
- An active extension tab can present the chooser and own Web Bluetooth.
- Windows Chrome fails unreliably around MITM-gated GATT access, while the same
  harness works over plain GATT.
- macOS did not reproduce the encrypted-GATT failure.
- Plain-GATT connection, synthetic authentication, pull, push, asynchronous
  copy, connector closure, and explicit reselection were exercised.
- One connector tab serving two Android phones was exercised on Windows and
  macOS.

These results justify continuing the tab-owned, plain-GATT design. They do not
authorize real OTP transport.

### Remaining feasibility gates

Issue #77 remains inconclusive until the following are recorded against exact
hardware, OS builds, stable Chrome versions, Bluetooth adapters, and commit
SHAs:

- One Android phone concurrently serving Windows and macOS, including
  independent pulls and one push reaching both clients.
- Android connected-device foreground-service survival through the documented
  20-minute task-removal and locked-screen scenario.
- A connector tab remaining connected and receiving pushes while backgrounded
  long enough to trigger Chrome timer throttling.
- Automatic copying from a backgrounded connector through the supported
  clipboard helper.
- Repeated action clicks focusing the existing connector tab rather than
  creating duplicates.
- Repeated closure, reconnection, and multi-device runs.

The previously reported behavior of already-bonded Windows devices is not a
general platform conclusion because exact environments and run sequences were
not recorded. Normal onboarding avoids OS pairing. Troubleshooting may advise
forgetting a stale bond when Windows cannot connect.

## Product flow

### First pairing

1. The user installs Veles Android and grants notification and Nearby Devices
   permissions.
2. The user installs Veles Chrome from the Chrome Web Store.
3. The user enables Desktop Sharing on Android. Android starts its foreground
   Bluetooth service and advertises the public Veles service.
4. The user starts Add Chrome on the unlocked phone. Android displays a short,
   expiring one-time code.
5. The user selects the extension action. Chrome opens or focuses the connector
   tab.
6. The user selects Connect phone and chooses the advertising phone in Chrome's
   chooser. No OS Bluetooth pairing step is required.
7. The user enters the Android code. Veles completes OPAQUE pairing and binds
   the two installation identities.
8. Both applications persist trust records and erase all one-time pairing
   state.

### Later use

1. The user selects the extension action. Chrome opens or focuses the connector
   tab.
2. If no live session exists, the user selects the phone in Chrome's chooser.
3. The peers authenticate automatically with their stored identities, derive
   fresh traffic keys, and establish a protected session.
4. Chrome requests eligible OTPs retained during the preceding ten minutes.
5. The connector tab may be backgrounded while the user returns to the target
   website.
6. Android pushes eligible new OTPs while the connector tab remains open and
   connected.
7. Chrome displays the data, updates its badge, and performs background
   automatic copy only when the user enabled that setting.

Closing the connector tab destroys connection state. Reopening it requires
explicit Chrome chooser selection, followed by automatic Veles authentication;
it does not require another one-time code unless trust was revoked or lost.

## Architecture

### Existing Android OTP path

The existing notification matching and local replacement notification remain
unchanged:

```text
Incoming notification
  -> NotificationListener
  -> bank handler chain
  -> matched OtpMessage
  -> existing local Veles notification and copy behavior
```

Desktop delivery is an additional consumer. Bluetooth failure never suppresses
the local Veles notification.

### Android desktop delivery

#### Delivery coordinator

The coordinator accepts matched OTP events, assigns stable event IDs, and
passes them to the memory buffer and eligible protected sessions. It keeps OTP
matching independent from Bluetooth lifecycle.

#### Memory-only OTP buffer

Android retains at most five OTP events globally for no more than ten minutes.
The buffer exists only while desktop sharing is enabled. Disabling sharing,
process death, or reboot clears it.

Android is authoritative for expiry and delivery eligibility. A response
includes remaining lifetime so Chrome cannot extend retention because of clock
skew.

#### Desktop-sharing service

A user-enabled `connectedDevice` foreground service owns:

- Bluetooth capability and permission state.
- Plain-GATT advertising and server lifecycle.
- Bounded transport framing and per-connection queues.
- Pairing and protected-session state.
- Multiple connected physical clients.
- Service and connection status exposed to Android UI.

The service starts from visible user action and persists the enabled preference.
Restoration after process death, reboot, package replacement, and Bluetooth
recovery is best effort where Android permits it. Permission loss, unsupported
advertising, OEM restriction, and Task Manager Stop are explicit unavailable
states.

Plain GATT exposes no OTP, trust record, meaningful error detail, or production
application message before Veles authentication. Bluetooth addresses are
connection hints only.

#### Trust storage

Android installation identity keys are non-exportable Android Keystore keys.
Peer trust metadata and keys are excluded from cloud backup and device transfer.
Missing, restored without keys, or corrupted trust state fails closed and
requires pairing again.

### Chrome extension

#### Project and build

The Manifest V3 extension is an npm/TypeScript project under repository-root
`src/typescript`. Gradle provides pinned, reproducible install, lint,
type-check, test, build, crypto, and package entry points. Rust/JNI/WASM details
follow the application security design.

#### Service worker

The MV3 service worker does not own Bluetooth or OTP session state. It:

- Opens the connector tab when none exists.
- Focuses the existing connector tab on repeated action clicks.
- Updates the toolbar badge from connector messages.
- Coordinates the supported offscreen clipboard helper.

#### Connector tab

The connector tab owns device selection, GATT objects, Veles pairing,
protected sessions, pull, push, and short-lived display history. It supports
multiple explicitly selected phones in one tab lifetime.

It must remain connected when backgrounded. Production liveness must not depend
on a five-second JavaScript interval that Chrome can throttle. The final
heartbeat, lease, or server-initiated liveness mechanism is selected only after
the revised background physical spike establishes reliable behavior.

Closing the tab ends all live sessions. The next tab starts without a reusable
`BluetoothDevice` object and requires explicit chooser selection.

#### Clipboard helper

Manual copy occurs from a focused extension UI. Background automatic copy is
delegated through the supported `chrome.offscreen` clipboard pattern. The
offscreen document handles clipboard work only; it never owns Bluetooth,
cryptographic sessions, or OTP history beyond the immediate copy operation.

Automatic copy is global and disabled by default. A successful pull copies its
newest OTP, including a repeated pull. A new push copies its OTP. Copy failures
are visible and never terminate the protected Bluetooth session.

#### Session history

Chrome keeps at most five OTPs globally across all phones for ten minutes in
browser-session storage. It merges events by phone installation ID and event
ID. No OTP is written to sync or durable local storage.

The badge shows the number of valid cached events. Closing Chrome or expiry
clears sensitive session data.

#### Chrome trust storage

Chrome generates a random installation ID and non-extractable identity key.
Private keys are stored as non-extractable Web Crypto keys in IndexedDB. Peer
metadata is local, non-sync extension storage restricted to trusted extension
contexts. Extension removal, profile data loss, or corruption requires pairing
again.

## Application security

Plain GATT is an actively attacked transport. The dedicated security design is
normative and requires:

- RFC 9807 OPAQUE for first pairing with the phone-generated one-time code.
- A pinned, reviewed Rust core compiled for Android JNI and locally packaged
  browser WASM.
- Persistent P-256 ECDSA installation identities.
- Fresh mutually signed P-256 ECDH on every connection.
- HKDF-SHA-256 directional key separation.
- AES-256-GCM protection of every production command, response, push, pull,
  heartbeat, acknowledgement, and error.
- Strict sequence, nonce, replay, ordering, size, and session checks.
- Explicit per-peer revocation and fail-closed storage loss.
- No production plaintext or hard-coded-test-key compatibility mode.

Real OTP integration is blocked until shared crypto vectors pass across Rust,
JNI, and WASM; protected sessions pass physical Windows/macOS testing; and the
release-candidate security review is accepted.

## Delivery policy

Android evaluates the configured policy immediately before every protected pull
response or push:

- **Unlocked only**, default: require an interactive and unlocked phone.
- **Display on**: require an interactive phone, including the lock screen.
- **Always**: allow delivery regardless of display or lock state.

An OTP received while blocked remains in the memory buffer until expiry. It may
be pulled after the phone becomes eligible. A blocked authenticated request
returns a protected phone-state response rather than appearing as empty history.

## Multiple connections

The intended stable topology is:

- One Android phone independently serving at least two physical desktop
  computers.
- One connector tab independently serving multiple Android phones.

Every peer has an independent installation identity, trust record, protected
session, sequence space, queue, revocation state, and failure boundary.
Concurrent Chrome profiles sharing one physical Bluetooth adapter remain best
effort unless later testing establishes a portable guarantee.

The multi-phone topology is promising from partial testing. The two-computer
topology remains an open Issue #77 gate.

## Sensitive-notification onboarding

Sensitive-notification companion association remains the existing Android
permission workaround. It may use any suitable nearby device and is independent
from Desktop Sharing.

The Chrome connector does not require, reuse, infer, or modify companion
association. A companion association never authorizes OTP delivery. Chrome
trust exists only after Veles OPAQUE enrollment.

## Failure handling

- Invalid, unknown, revoked, malformed, replayed, reordered, oversized, or
  incompatible protocol input returns no OTP data and fails closed.
- Bluetooth unavailable, Nearby Devices denial, unsupported advertising,
  service failure, and connection-capacity limits have distinct Android states.
- Chrome distinguishes unsupported Web Bluetooth, chooser cancellation,
  unavailable phone, stale-bond troubleshooting, policy denial, authentication
  failure, empty history, background liveness failure, clipboard failure, and
  disconnect.
- Background-tab timer throttling must not silently convert a healthy session
  into expected behavior. It is a transport failure until the revised spike
  proves a robust mechanism.
- Disconnection abandons pending delivery. A new chooser selection establishes
  fresh session keys and performs a fresh pull.
- OTPs, pairing codes, keys, public-key encodings, and plaintext records never
  appear in logs or metrics.

## Validation strategy

### Current spike completion

Issue #77 first revises its synthetic harness and matrix to:

- Remove required OS pairing and encrypted-GATT expectations.
- Test tab backgrounding for a throttling-relevant duration.
- Replace the short client timer dependency with the proposed production
  liveness approach.
- Route automatic copy through the offscreen clipboard helper.
- Run one-phone/two-computer fan-out.
- Run the 20-minute Android foreground lifecycle.
- Repeat closure and multi-device cases.
- Record exact environments, commit SHAs, timings, logs, and limitations.

The spike reaches **go** only when every required Windows/macOS case passes.
A harness defect is fixed and rerun. Missing hardware or incomplete evidence is
inconclusive. A repeatable failure returns the transport design for review.

### Automated security and feature validation

- Shared deterministic vectors cover OPAQUE, identity signatures, ECDH, HKDF,
  AEAD, replay state, binary schemas, and JNI/WASM boundaries.
- Property and fuzz tests cover binary decoders, fragmentation, state machines,
  and length bounds.
- Android tests cover secure storage and backup exclusion, buffer expiry,
  delivery policy, service lifecycle, pairing limits, revocation, and fan-out.
- Extension tests cover installation identity persistence, launcher behavior,
  connector lifecycle, background messaging, clipboard helper, session history,
  badge, copying, multiple phones, and errors.
- Docker and CI validate deterministic builds and simulated protocol behavior;
  they never claim to validate physical Bluetooth.

### Physical release validation

Physical Windows and macOS testing covers one-time OPAQUE pairing, automatic
authenticated reconnect, encrypted pull and push, background-tab delivery,
background automatic copy, interruption, closure, browser restart, revocation,
multiple peers, delivery policies, malformed traffic, replay, and Android
foreground lifecycle. Linux remains experimental and requires separate
documented validation.

## Documentation and privacy

README and GitHub Pages must explain:

- The connector-tab requirement and explicit chooser selection.
- That the tab may remain backgrounded but must stay open.
- Plain Bluetooth transport versus encrypted Veles application records.
- One-time Veles pairing and automatic authenticated reconnect.
- No OS Bluetooth pairing prerequisite.
- Revocation and recovery after key loss.
- Windows stale-bond troubleshooting.
- Foreground-service behavior and supported platforms.
- The independent sensitive-notification companion fallback.

Privacy language must distinguish no internet transmission from explicit,
opt-in, encrypted local Bluetooth sharing.

## Provisional implementation workstreams

The following workstreams are justified by current evidence but are not yet an
issue-ready execution order:

1. Complete Issue #77's remaining transport and lifecycle gates.
2. Establish reproducible TypeScript, Rust, NDK/JNI, and WASM toolchains.
3. Freeze and externally review the security protocol profile and schemas.
4. Implement and cross-test the OPAQUE core.
5. Implement installation identity and trust stores.
6. Build production Android plain-GATT transport and Chrome connector-tab
   transport without exposing OTPs.
7. Implement one-time enrollment, authenticated reconnect, and protected
   records.
8. Add trust management and revocation.
9. Add the OTP buffer, delivery policy, protected pull, and protected push.
10. Establish automated and physical secure end-to-end validation.
11. Add Android and Chrome product UI, history, badge, and copy behavior.
12. Validate multi-peer isolation and harden lifecycle and failure handling.
13. Complete release-candidate security review, packaging, platform validation,
    Web Store disclosures, and documentation.

No issue that sends, displays, or copies real OTP data may precede the protected
record layer. No release packaging may precede the release-candidate security
review.

## Backlog and project finalization

Do not create the remaining implementation issues or GitHub project while
Issue #77 is inconclusive. After the spike reaches a supported decision:

1. Reconcile these workstreams with the observed transport constraints.
2. Produce concise, standalone issues with explicit dependencies and security
   gates.
3. Create the public GitHub project under `raidenyn`.
4. Add implementation order, phase, area, release scope, and status fields.
5. Put deferred offscreen Bluetooth ownership and desktop notifications outside
   the stable-release path.

## Reference constraints

- [Chrome Web Bluetooth](https://developer.chrome.com/docs/capabilities/bluetooth)
- [Chrome extension service-worker lifecycle](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle)
- [Chrome offscreen documents](https://developer.chrome.com/docs/extensions/reference/api/offscreen)
- [MDN Web Bluetooth API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Bluetooth_API)
- [Android Bluetooth permissions](https://developer.android.com/develop/connectivity/bluetooth/bt-permissions)
- [Android BLE background guidance](https://developer.android.com/develop/connectivity/bluetooth/ble/background)
- [Android connected-device foreground services](https://developer.android.com/develop/background-work/services/fgs/service-types#connected-device)
- [Android companion-device pairing](https://developer.android.com/develop/connectivity/bluetooth/companion-device-pairing)
