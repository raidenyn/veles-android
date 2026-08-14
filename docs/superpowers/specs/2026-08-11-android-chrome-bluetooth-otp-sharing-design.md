# Android-Chrome Bluetooth OTP sharing roadmap

## Status

Approved on 2026-08-11.

> **Superseded transport and security assumptions:** Physical validation ruled
> out action-popup Web Bluetooth and authenticated/MITM GATT permissions on
> Windows. The connector now requires an active extension tab and plain GATT;
> the approved one-time OPAQUE pairing, persistent identity, signed session-key
> exchange, AEAD, and replay design is specified in
> `2026-08-14-bluetooth-application-security-design.md`. References below to a
> popup, OS pairing as a prerequisite, encrypted GATT, or an unspecified PAKE
> are retained as historical roadmap context and do not override that design.

This document is a roadmap specification. It defines the product boundaries, architecture,
security model, delivery order, and release gates for Bluetooth OTP sharing between Veles
Android and a Veles Chrome extension. Each roadmap issue receives its own focused design
before implementation. The issue descriptions intentionally avoid low-level implementation
details.

## Goal

Let a user explicitly connect a Chrome extension to Veles Android over local Bluetooth and
retrieve recent OTP transaction data. While the extension popup remains connected, Android
also pushes new OTPs immediately. The extension displays the OTP code, merchant, and amount
and supports manual or opt-in automatic copying.

The feature remains local. It introduces no internet transport, cloud service, account,
telemetry, or native desktop application.

## Product flow

1. The user installs Veles Android and grants its required notification permissions.
2. The phone and desktop are paired through their operating-system Bluetooth settings.
3. The user installs Veles Chrome from the Chrome Web Store.
4. Android and Chrome complete a separate Veles pairing flow using a one-time code.
5. The user opens the extension popup, selects a paired phone through Chrome's Bluetooth
   chooser, and connects.
6. Chrome requests eligible OTPs retained during the preceding ten minutes.
7. While the popup remains open, Android pushes eligible new OTPs to every connected client.
8. Chrome displays the OTP data and lets the user copy it. Automatic copying is available as
   a global, disabled-by-default setting.

## Constraints

### Chrome lifecycle

Manifest V3 service workers cannot own Web Bluetooth objects. A toolbar popup can use Web
Bluetooth, but its GATT connection belongs to that popup document and is lost when the popup
closes. Stable Chrome APIs also do not let an extension silently select the last device after
the popup has been recreated.

The stable release therefore uses an explicit connection for each popup session:

- The user clicks Connect and selects a Veles phone in Chrome's chooser.
- Chrome pulls recent OTPs after authentication.
- Android pushes new OTPs only while the popup remains connected.
- Closing the popup ends useful push delivery, even if the platform takes time to report the
  underlying disconnect.
- A heartbeat and session timeout remove abandoned sessions on Android.

A later experiment may evaluate an offscreen extension document and experimental device
recovery APIs. Stable behavior does not depend on that experiment.

### Desktop platforms

Windows and macOS stable Chrome are the initial supported desktop platforms. Linux remains
experimental because current Chrome releases require an experimental Web Bluetooth flag.
Chrome Web Store distribution is the stable release channel; developer-mode installation is
used only for development and testing.

### Android availability

For Chrome to connect on demand, Android must advertise while desktop sharing is enabled.
A user-enabled connected-device foreground service owns advertising, the GATT server, active
sessions, and client delivery queues. It supplies the required ongoing service indication.

Service restoration after ordinary process death, reboot, package replacement, or Bluetooth
recovery is best effort. Permission revocation, unsupported peripheral advertising, Android
Task Manager Stop, and OEM background restrictions are explicit unavailable states. Veles
never reports desktop sharing as available when the service is not actually running.

## Scope

### Stable release

- Android BLE peripheral advertising and GATT service.
- Veles application pairing and authenticated sessions.
- Pulling up to five recent OTPs retained for no more than ten minutes.
- Pushing new OTPs while an extension popup remains connected.
- Manual copy and disabled-by-default automatic copy in Chrome.
- Android desktop-sharing and client-management UI.
- Chrome pairing, connection-management, OTP popup, and settings UI.
- One phone serving at least two physical desktop computers concurrently.
- One popup explicitly connecting to multiple phones during the same popup session.
- Gradle integration for a TypeScript npm project under repository-root `src/typescript`.
- Windows and macOS validation, Chrome Web Store packaging, and experimental Linux guidance.
- README and GitHub Pages documentation.

### Deferred work

- Offscreen-document ownership and automatic last-device recovery.
- Chrome or operating-system OTP notifications.
- A native desktop connector.
- Cloud relay, internet transport, accounts, or synchronization.
- Guaranteed concurrent sessions for multiple Chrome profiles sharing one Bluetooth adapter.
- Durable OTP history on Android or Chrome.

## Architecture

### Existing OTP path

The existing Android matching and local notification behavior remains unchanged:

```text
Incoming notification
  -> NotificationListener
  -> bank handler chain
  -> matched OtpMessage
  -> existing local Veles notification and copy behavior
```

Desktop delivery is an additional consumer of a matched `OtpMessage`. Desktop failures never
prevent the local Veles notification.

### Android components

#### Desktop delivery coordinator

The coordinator accepts matched OTP events, assigns stable event identities, and passes them
to the short-lived buffer and eligible connected clients. It isolates notification matching
from Bluetooth service lifecycle.

#### OTP buffer

The buffer is process memory only. It retains at most five OTP events globally for no more
than ten minutes. It runs only while desktop sharing is enabled and is cleared immediately
when sharing is disabled. Process death and reboot also clear it.

Each event has a stable ID, receipt time, code, merchant, amount, and currency. Android is the
authority for eligibility and expiry. Chrome merges events by phone identity and event ID so
repeated pulls do not duplicate the displayed history.

#### Desktop-sharing service

The connected-device foreground service owns:

- Bluetooth capability and permission checks.
- BLE advertising and the GATT server.
- Authenticated sessions and heartbeat expiry.
- Per-client subscriptions, framing, sequencing, and delivery queues.
- Fan-out to eligible connected clients.
- Service and connection status for the Android UI.

The service starts from visible user action. Enabling is persisted, and restoration is
best-effort where Android permits it. Disabling closes sessions, stops advertising, clears
the OTP buffer, and retains trusted clients until the user revokes them.

#### Trust store

Android persists trusted client identity and key material, not OTP data. Secret material is
protected by Android Keystore and excluded from backup and device transfer. Restored metadata
without its key, application reinstall, or key loss invalidates the trust record and requires
pairing again.

#### Desktop Connections UI

The existing Home screen gains a Desktop Sharing card that opens a dedicated Connections
screen. No fourth bottom-navigation destination is added. The screen provides:

- Sharing enablement and actual service status.
- Pair Chrome action.
- Trusted and currently connected client list.
- Client revocation.
- Delivery-policy selection.
- Actionable Bluetooth, permission, and service errors.

The foreground-service indication opens this screen and reports sharing and connection state.

### Chrome extension components

#### TypeScript project

The Manifest V3 extension lives in an npm project at repository-root `src/typescript`.
Gradle exposes locked, reproducible entry points for install, lint, type-check, test, build,
and package operations. The extension requests only permissions required for Bluetooth,
session history, badge, settings, and copying.

#### Visible-page Bluetooth transport

Only visible extension documents own Web Bluetooth:

- The settings page owns the temporary connection used for initial pairing, then disconnects.
- The toolbar popup owns each normal pull/push session.
- Bluetooth objects are never transferred to the service worker or another document.

The transport filters Chrome's chooser to the Veles service, authenticates the selected
phone, negotiates protocol capabilities, pulls eligible history, and listens for pushes until
the owning document closes.

#### Popup

The popup:

1. Shows cached, unexpired session history immediately.
2. Offers Connect for each phone the user wants to use in that session.
3. Authenticates and pulls recent eligible OTPs.
4. Displays code, merchant, amount, age, and source phone.
5. Offers a Copy action for each OTP.
6. Receives pushes while open.

Chrome keeps no more than five OTPs for ten minutes in browser-session storage, never durable
storage. This is one global five-event limit across all connected phones. Android includes the
remaining lifetime of each event so Chrome cannot extend retention because of clock skew. The
toolbar badge shows the count of valid cached OTPs and clears as entries expire. It cannot
update from Android while no extension document is connected.

#### Settings and installation identity

The settings page starts initial pairing, manages remembered phones, revokes local trust, and
owns the global automatic-copy toggle. Chrome generates a random per-installation identity
and keeps identity and keys in non-sync local storage. Clearing extension data or reinstalling
the extension requires pairing again.

Automatic copy is disabled by default. When enabled:

- Every successful pull copies its newest OTP, including a repeated pull.
- Every newly pushed OTP is copied.
- Repeated pull responses merge by event ID and do not duplicate displayed history.

## Pairing and security

### Trust model

Operating-system Bluetooth pairing and encrypted GATT are prerequisites and defense in depth,
but are not the root of Veles application trust. A CompanionDeviceManager association does
not itself authorize OTP delivery.

Veles pairing uses an established password-authenticated key exchange (PAKE) based on the
one-time Android code. The protocol design issue selects and reviews the concrete PAKE with
compatible Kotlin and TypeScript implementations. A custom cryptographic handshake is not
acceptable. The exchange performs explicit key confirmation and binds both installation
identities and the negotiated protocol transcript.

### Pairing flow

1. The user opens Add Chrome on Android.
2. Android enters a two-minute pairing window and displays a random six-digit code.
3. The user starts Pair Phone in the Chrome settings page, selects the advertising phone, and
   enters the Android code.
4. Chrome contributes its random installation identity and proposed client label.
5. Android displays the client and requires final user approval.
6. The PAKE authenticates the short code without exposing it to offline guessing and derives
   the client trust material.
7. Both sides persist the resulting identity and keys; the code and pending session expire.

Pairing is rate-limited, and the code is invalidated on success, cancellation, timeout, or too
many attempts.

### Authenticated sessions

Later connections authenticate the trusted installation, derive fresh session keys, and
negotiate compatible protocol capabilities. OTP messages use authenticated encryption,
unique nonces, sequence numbers, and replay rejection. Exact Android and extension versions
need not match; delivery fails only when the peers share no supported protocol version.

Unknown, revoked, unauthenticated, replayed, malformed, oversized, expired, or incompatible
requests return no OTP data. OTPs, pairing codes, and keys are never logged.

### Revocation

Revoking a client deletes its key and disconnects its sessions. A connected counterpart can
request mutual removal. Offline removal is local, and the UI explains that the counterpart
may also need removal when it is next available.

### Threat boundary

The design protects against unintended nearby devices, short-code offline guessing, message
tampering, and replay. It does not protect OTPs after compromise of the unlocked phone,
Chrome profile, extension, browser, operating system, or clipboard.

## Delivery policy and data flow

### Policies

Android evaluates the selected policy immediately before every pull response and push:

- **Unlocked only**, default: require an interactive and unlocked phone.
- **Display on**: require an interactive phone, including the lock screen.
- **Always**: allow delivery regardless of display or lock state.

An OTP that arrives while delivery is blocked remains in the memory buffer until expiry. It
can be pulled later once the phone becomes eligible. A blocked pull returns a distinct
phone-state response rather than appearing to have an empty history.

### Pull

```text
User opens popup and clicks Connect
  -> Chrome chooser selects a Veles phone
  -> authenticated session established
  -> Chrome requests recent OTPs
  -> Android evaluates current delivery policy
  -> Android returns up to five eligible, unexpired events
  -> Chrome merges session history and updates the badge
  -> Chrome copies the newest response when auto-copy is enabled
```

### Push

```text
Android matches a new OTP
  -> local Veles behavior continues
  -> event enters the memory buffer when sharing is enabled
  -> Android evaluates current delivery policy
  -> eligible event is encrypted and sent to every authenticated client
  -> each client acknowledges independently
  -> connected popup displays and optionally copies the event
```

A failed or slow client never blocks another client or the local Android notification.

## Multiple connections

The first release guarantees the following tested topology:

- One Android phone serves at least two physical desktop computers concurrently on supported
  hardware.
- One Chrome popup can explicitly select and connect to multiple phones during its lifetime.

Different Chrome profiles generate separate Veles installation identities, but concurrent
profiles sharing one physical Bluetooth adapter are best effort. Android tracks application
identity separately from Bluetooth device address, which is not treated as stable trust.

## Sensitive-notification onboarding

When sensitive-notification access is missing, the desktop setup flow attempts to reuse the
same physical computer in the existing Android companion-association flow. The system
association remains permission-only and never grants Veles protocol trust.

The existing path for selecting any nearby Bluetooth device remains available to users who
do not have the Chrome extension. This work coordinates with GitHub issue #43 and does not
turn headphones, cars, watches, or other permission-only associations into OTP recipients.

## Failure handling

- Bluetooth unavailable, missing Nearby Devices permission, unsupported advertising, failed
  advertising, and connection-capacity limits have distinct Android states.
- Chrome distinguishes unsupported Web Bluetooth, Linux flag requirements, chooser
  cancellation, unavailable phone, policy denial, authentication failure, empty history, and
  mid-transfer disconnect.
- Disconnection abandons pending delivery. The next popup connection performs a fresh pull.
- Android process death clears OTP history and connections. Browser restart clears Chrome OTP
  history. Trusted identities and settings remain unless their keys are unavailable.
- BLE framing is bounded and handles negotiated packet-size limits. Oversized or malformed
  messages are rejected.
- No error, debug log, metric, or status text includes plaintext OTPs, codes, or keys.

## Validation strategy

### Feasibility gate

The first roadmap issue established these binding platform findings:

- An MV3 action popup cannot own `requestDevice()` on desktop Chrome; the supported visible
  owner is an active extension tab.
- Chrome Web Bluetooth on Windows cannot reliably access authenticated/MITM-gated GATT
  attributes, including already-paired devices. The transport uses plain GATT permissions.
- macOS does not reproduce the Windows encrypted-GATT failure.
- Plain-GATT pull, push, asynchronous copy, connector closure, and two-phone flows are viable
  enough to continue the roadmap.
- The 20-minute foreground lifecycle and one-phone/two-connector topology remain unverified
  and are release-gate work, not assumed successes.

Plain GATT is transport only. No real OTP may cross it until the application-layer protocol in
`2026-08-14-bluetooth-application-security-design.md` provides trusted pairing, persistent
identity, fresh authenticated session keys, AEAD, and replay protection. Experimental offscreen
APIs remain outside the stable-release architecture.

### Automated validation

- RFC 9807 and project protocol vectors are consumed by native Rust, Android JNI, and browser
  WASM; platform-crypto vectors cover identity signatures, ECDH, HKDF, AEAD, and replay state.
- Android tests cover secure storage and backup exclusion, buffer expiry, policy checks,
  pairing limits, authentication, revocation, fan-out, and service state.
- Extension tests cover non-extractable identity persistence, connector-tab state, expiry,
  deduplication, badge count, copying, multiple phones, and errors.
- Packaged-extension browser tests use simulated transport where practical.
- Docker CI validates deterministic builds and simulation, but never claims to validate real
  Bluetooth behavior.

### Physical-system validation

A repeatable hardware workflow validates one-time OPAQUE pairing, automatic authenticated
reconnect, encrypted pull and push, disconnect, revocation, and recovery using a physical
Android 13+ phone and desktop Chrome. Release validation covers Windows, macOS, experimental
Linux, two concurrent computers, multiple phones, all delivery policies, Bluetooth
interruption, connector-tab/browser closure, process restart, malformed traffic, replay
attempts, and the previously untested 20-minute lifecycle and two-connector topology.

Issue 3 ends with a focused protocol review before cryptographic implementation. Issue 21
performs the external release-candidate security review, and Issue 22 cannot begin until that
review is accepted.

## Documentation and privacy

README and GitHub Pages documentation must explain installation, Chrome device selection,
one-time Veles pairing, automatic secure reconnect, security boundaries, supported platforms,
foreground-service behavior, connector-tab limitations, Linux experimental setup, and the
any-device Android fallback.

The current privacy statement must distinguish no internet transmission from explicit,
opt-in, encrypted local Bluetooth sharing. Veles remains free of telemetry, analytics,
accounts, advertisements, payments, and cloud services.

## Ordered roadmap

Issues 1 through 23 form the stable-release path. Issues 24 and 25 are deferred and do not
block that release. Every issue that sends or displays OTP data depends on Issue 9's protected
record layer. Production code has no plaintext or hard-coded-test-key compatibility mode.

1. **Spike: Conclude visible-tab Web Bluetooth transport validation**
   Record the action-popup and Windows encrypted-GATT failures, the viable plain-GATT flows,
   the untested topologies, and the conditional recommendation. The spike remains synthetic.
2. **Tech: Add Chrome and Rust crypto toolchains to the reproducible Gradle build**
   Establish the Manifest V3 TypeScript/npm project plus pinned Rust, Android NDK/JNI, and
   packaged WASM build entry points. Enforce no remote executable code and emit reproducible
   artifact hashes and dependency metadata.
3. **Design: Freeze and review the secure Veles Bluetooth protocol**
   Adopt `2026-08-14-bluetooth-application-security-design.md`, freeze the OPAQUE profile,
   binary schemas, identities, key schedule, limits, and error model, and obtain a focused
   pre-implementation review. Protocol ambiguities block Issues 4, 8, and 9.
4. **Crypto: Build the shared RFC 9807 OPAQUE core for Android and Chrome**
   Implement one pinned Rust wrapper exposed only through complete JNI and WASM operations.
   Pass RFC and project fixed-randomness vectors identically on native Rust, Android, and
   Chrome; cover malformed state, zeroization paths, KSF performance, and packaged-WASM policy.
5. **Security: Add persistent installation identity and trust stores**
   Generate non-exportable P-256 ECDSA installation keys, Android Keystore and IndexedDB
   persistence, per-peer public records, non-sync Chrome metadata, Android backup/transfer
   exclusions, storage-loss fail-closed behavior, and cross-platform storage tests.
6. **Android: Add the desktop-sharing Bluetooth transport service**
   Add the user-enabled foreground service, advertising, plain-GATT lifecycle, availability,
   and bounded transport framing. The service exposes no OTP data before application-layer
   authentication and never treats a Bluetooth address as trust identity.
7. **Chrome: Add visible-tab Bluetooth connections**
   Add action-launched connector-tab selection, connection lifecycle, repeated-action focus,
   and explicit reselection after closure. Keep Bluetooth ownership visible and do not add an
   offscreen stable-release fallback.
8. **Security: Implement one-time OPAQUE pairing and identity enrollment**
   Add phone-generated expiring codes, local OPAQUE pre-registration, online attempt limits,
   mutual confirmation, encrypted identity enrollment, pending/commit/ack state, generic
   failures, secret erasure, and smooth re-pairing after revocation or storage loss.
9. **Security: Implement authenticated reconnect and the protected record layer**
   Reuse persistent identity keys without another user challenge, perform signed fresh P-256
   ECDH after every Chrome device selection, derive directional AES-256-GCM keys with HKDF,
   and encrypt every command, response, push, pull, heartbeat, acknowledgement, and error.
   Enforce strict sequence/replay rejection, nonce uniqueness, canonical encoding, and no
   downgrade to plaintext application records.
10. **Feature: Manage and revoke trusted Veles devices**
    Add peer listing, fingerprints, labels, last-seen state, key-loss handling, and connected
    or offline per-peer revocation. Revocation immediately blocks sessions and requires new
    one-time pairing.
11. **Android: Add the short-lived OTP delivery buffer and phone-state policy**
    Add memory-only retention and the unlocked, display-on, and always policies. Keep OTPs out
    of logs, durable storage, pairing messages, and unauthenticated transport state.
12. **Feature: Pull recent OTPs from Android into Chrome**
    Retrieve eligible recent OTP events only through Issue 9 protected records, with bounded
    responses, authenticated empty-history behavior, and no plaintext fallback.
13. **Feature: Push new OTPs to connected Chrome clients**
    Deliver eligible matched OTPs independently to every authenticated encrypted session,
    handling per-client failure without exposing or blocking another client.
14. **Testing: Establish secure Android-Chrome end-to-end validation**
    Add deterministic protocol simulation, JNI/WASM interoperability, storage and backup,
    wrong-code, MITM, modification, replay, reorder, crash/restart, revocation, pull/push, and
    real-hardware Windows/macOS workflows. Automated tests never claim physical Bluetooth.
15. **Android UI: Add Desktop Sharing and Connections settings**
    Add service, policy, one-time pairing, countdown/attempt state, connection status,
    fingerprints, trusted-client, storage-loss, and revocation UI without displaying secrets
    in logs or screenshots beyond the active pairing code.
16. **Android: Integrate Chrome pairing with sensitive-notification onboarding**
    Coordinate desktop sharing with the existing onboarding while retaining the any-device
    fallback. Companion association is optional defense in depth and not a transport-security
    prerequisite.
17. **Chrome UI: Add OTP display, copy actions, history, and badge**
    Build the Android-aligned frequent-use experience and short-lived session history. Render
    OTP data only after protected-record authentication and preserve clipboard focus/error
    behavior.
18. **Chrome UI: Complete connection, trust, and auto-copy settings**
    Add paired-phone management, fingerprints, revocation/re-pairing, secure reconnect status,
    and global disabled-by-default automatic copying. Never expose private key material.
19. **Feature: Support multiple simultaneous Android and Chrome connections**
    Validate independent identities, traffic keys, counters, queues, revocation, and failure
    isolation for multiple phones and multiple connectors, including the previously untested
    one-phone/two-connector topology.
20. **Hardening: Secure Bluetooth lifecycle and failure handling**
    Cover interruption, connector/background lifecycle, process death, malformed crypto and
    framing input, global/per-peer limits, rate limiting, safe logs, key erasure, counter
    boundaries, recovery, and the previously untested 20-minute foreground scenario.
21. **Security: Review the release-candidate protocol and crypto supply chain**
    Obtain external review of the exact OPAQUE wrapper/dependencies, JNI/WASM boundaries,
    platform key storage, enrollment, signed ECDH, key schedule, AEAD nonces, replay policy,
    revocation, binary decoders, CSP, SBOM, and built artifacts. Unresolved findings block
    release packaging and all real-OTP enablement.
22. **Release: Validate and package Chrome platform support**
    Complete CI, reproducible APK/extension builds, Windows/macOS and experimental Linux
    validation, Chrome Web Store disclosures, local-WASM policy checks, and extension
    packaging only after Issue 21 is accepted.
23. **Docs: Add Veles Chrome to the README and GitHub Pages**
    Document connector-tab behavior, plain BLE transport versus encrypted application data,
    one-time pairing and automatic reconnect, revocation, supported platforms, security
    boundaries, privacy language, and recovery after key loss.
24. **Experiment: Automatically reconnect through an offscreen extension document**
    Evaluate experimental hidden ownership and last-device recovery after stable release.
    Reuse Issue 9 security unchanged; hidden transport never weakens authentication.
25. **Feature: Show desktop OTP notifications**
    Add optional Chrome or operating-system notifications only after Issue 24 establishes a
    viable background transport; otherwise return notification delivery for redesign. Consume
    only authenticated protected records.

### Security dependency gates

- Issues 4 and 5 require Issue 3.
- Issues 8 and 9 require Issues 4 through 7.
- Issue 10 requires Issues 5, 8, and 9.
- Issues 12, 13, 17, 18, 19, 20, 24, and 25 require Issue 9.
- Issue 14 validates Issues 4 through 13 and remains active as later UX work lands.
- Issue 21 reviews the integrated release candidate after Issues 2 through 20.
- Issue 22 requires accepted Issue 21 findings; production OTP enablement is part of Issue 22
  and cannot be merged earlier.

## GitHub project

Create a public GitHub Projects v2 project under `raidenyn` and link it to
`raidenyn/veles-android`. Add all roadmap issues in implementation order.

The project uses these fields:

- **Status**: Backlog, Ready, In Progress, In Review, Blocked, Done.
- **Implementation order**: numeric order from 1 through 25.
- **Phase**: Feasibility, Foundations, Security, Core delivery, Product UX, Release, Future.
- **Area**: Android, Chrome, Cryptography, Cross-platform, Documentation.
- **Release scope**: Stable release or Deferred.

The existing spike Issue 1 starts In Review until its conditional report is accepted; all
new issues start Backlog. The project provides an implementation-order table, a status board,
a security-gates view, and a deferred-work view. If the GitHub API cannot configure views, the
populated fields, dependency text, and sequential item positions remain authoritative and view
setup is documented for a project owner to complete in the web UI.

## Reference constraints

- [Chrome Web Bluetooth](https://developer.chrome.com/docs/capabilities/bluetooth)
- [Chrome extension service-worker lifecycle](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle)
- [Chrome offscreen documents](https://developer.chrome.com/docs/extensions/reference/api/offscreen)
- [MDN Web Bluetooth API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Bluetooth_API)
- [Android Bluetooth permissions](https://developer.android.com/develop/connectivity/bluetooth/bt-permissions)
- [Android BLE background guidance](https://developer.android.com/develop/connectivity/bluetooth/ble/background)
- [Android connected-device foreground services](https://developer.android.com/develop/background-work/services/fgs/service-types#connected-device)
- [Android companion-device pairing](https://developer.android.com/develop/connectivity/bluetooth/companion-device-pairing)
