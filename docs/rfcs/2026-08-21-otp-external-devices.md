# RFC: Veles OTP external devices

## Status

Accepted for execution planning. Amended to select the native bridge on
2026-08-21, to select RFC 9382 SPAKE2 for first pairing on 2026-08-25, and to
add the T-WATCH-S3 watch client with emulator-first execution on 2026-08-25;
all amendments are consolidated into this body.

This RFC is the canonical product and technical specification for sharing OTP
transaction data between Veles Android and locally connected external devices:
a Veles Chrome extension today, and a LILYGO T-WATCH-S3 family (ESP32-S3)
smartwatch whose firmware lands in a follow-up amendment after the feasibility
spikes (OTP-26/OTP-27). It was originally introduced in pull request
[#76](https://github.com/raidenyn/veles-android/pull/76) as the OTP Chrome
extension RFC and consolidated the roadmap, security design, implementation
spike, and physical observations from pull request
[#78](https://github.com/raidenyn/veles-android/pull/78).

Where earlier roadmap or spike documents conflict with this RFC, this RFC takes
precedence. The earlier documents remain historical evidence and implementation
background.

PR #78 recorded an incomplete physical validation matrix and described
tab-owned Web Bluetooth as conditionally promising. That evidence remains
useful history, but the approved amendments to this RFC replace that production
transport on Windows and macOS with an on-demand Rust and Tauri Native Messaging
BLE bridge with no durable state, replace OPAQUE enrollment with balanced
SPAKE2, and add the watch client protocol surface. Chrome continues to own all
Veles cryptography on the desktop; the watch reuses the same SPAKE2 core and
protected plain-GATT transport shape. These decisions permit execution
planning. They do not waive native-host feasibility, protected-transport,
security-review, physical-validation, installer, or release gates defined
below.

## Decision summary

- Veles provides clients for local OTP delivery from Veles Android: a Manifest
  V3 Chrome extension (desktop client) and a LILYGO T-WATCH-S3 family
  ESP32-S3 smartwatch (watch client). The Android protocol machinery is
  device-agnostic; "client" refers to either peer type.
- The extension action opens or focuses one dedicated connector tab. The tab
  owns search, pairing, connection, recovery, and OTP presentation UI.
- The connector tab owns one Chrome Native Messaging port to an on-demand,
  headless Rust and Tauri bridge. The bridge owns only OS BLE discovery and
  plain-GATT connections; it is not a persistent daemon and stores no durable
  state or payload.
- Android exposes a plain-GATT BLE peripheral through a user-enabled
  `connectedDevice` foreground service.
- Plain GATT is untrusted transport. OS Bluetooth pairing, Bluetooth names,
  Bluetooth addresses, and Android companion associations provide no Veles
  trust.
- First pairing uses RFC 9382 balanced SPAKE2 and a short code displayed by the
  unlocked phone. Later use searches or reconnects through non-authoritative
  platform hints and always authenticates the phone before accepting a session.
  The watch enters the same code on an on-screen dial; pairing limits and
  windows are identical for both client types.
- Every production application record is encrypted, authenticated, ordered,
  and replay protected independently of BLE link security.
- Android and each client retain at most five OTP events globally for no more
  than ten minutes; the watch keeps them RAM-only. OTP history is never
  durable on any endpoint.
- Manual copy is supported on desktop. Automatic copy is global, opt-in, and
  disabled by default. Background automatic copy uses a clipboard-only
  offscreen document.
- Per-user signed installers register the exact production extension origin
  with Chrome on Windows and macOS. Linux implementation and support are wholly
  deferred to a future RFC.
- Cloud transport, internet synchronization, accounts, and telemetry are
  permanently prohibited. OTP data must remain on the user's local endpoint
  devices and must never transit an internet service.
- The native bridge never receives trust records, private identity keys,
  traffic keys, pairing codes, decrypted Veles records, or OTP plaintext.
- A watch's local PIN (if enabled) never leaves the watch, is absent from every
  protocol message, and a forgotten PIN requires re-pairing.

## Source evidence

The decisions in this RFC are based on the following sources:

- [PR #78: tab-owned Web Bluetooth transport spike](https://github.com/raidenyn/veles-android/pull/78)
- [Issue #77: remaining transport validation](https://github.com/raidenyn/veles-android/issues/77)
- [Original Android-Chrome sharing roadmap](../superpowers/specs/2026-08-11-android-chrome-bluetooth-otp-sharing-design.md)
- [Original popup feasibility design](../superpowers/specs/2026-08-11-popup-web-bluetooth-spike-design.md)
- [PR #78 application-security design](https://github.com/raidenyn/veles-android/blob/f5ec4afbd587c57e844d9d58b635211dde86f9c3/docs/superpowers/specs/2026-08-14-bluetooth-application-security-design.md)
- [PR #78 physical-validation report](https://github.com/raidenyn/veles-android/blob/f5ec4afbd587c57e844d9d58b635211dde86f9c3/docs/spikes/2026-08-11-popup-web-bluetooth-validation.md)
- [RFC 8125: Requirements for PAKE schemes](https://www.rfc-editor.org/rfc/rfc8125.html)
- [RFC 9382: SPAKE2](https://www.rfc-editor.org/rfc/rfc9382.html)
- [RFC 9807: OPAQUE](https://www.rfc-editor.org/rfc/rfc9807.html)
- [CPace CFRG Internet-Draft](https://datatracker.ietf.org/doc/draft-irtf-cfrg-cpace/)
- [PR #79 first-pairing protocol review](https://github.com/raidenyn/veles-android/pull/79#discussion_r3834994122)

PR #78 physically established or strongly supported these findings:

- Stable desktop Chrome does not present `requestDevice()` from an MV3 action
  popup. An active extension tab can present the chooser.
- Windows Chrome cannot reliably use GATT attributes gated by authenticated BLE
  pairing. The same harness works over plain GATT.
- Plain-GATT synthetic connection, authentication, pull, push, asynchronous
  copy, closure, and explicit reselection worked in partial Windows and macOS
  runs.
- One connector tab serving two Android phones worked in partial Windows and
  macOS runs.
- Background-tab liveness, offscreen background copy, one phone serving two
  computers, repeated runs, and the full Android foreground-service lifecycle
  were not completely recorded.

Those observations explain why the earlier design selected a tab-owned
transport; they do not define the amended production architecture. Native-host
feasibility and production-path testing remain mandatory before release.

## Goals

- Let a user explicitly pair a Veles client installation (Chrome extension or
  T-WATCH-S3 watch) with a Veles Android installation over nearby Bluetooth.
- Let clients retrieve recent eligible OTP transaction data from Android.
- Let Android push new eligible OTPs while the client remains connected,
  including to a backgrounded connector tab.
- Display the OTP code, merchant, amount, age, and source phone on the client;
  on the watch, optionally behind a local PIN.
- Support manual copy and disabled-by-default automatic copy on desktop.
- Support one phone serving at least two physical clients concurrently and one
  connector tab and bridge process serving multiple phones independently.
- Keep the feature local, short-lived, revocable, and independent from the
  existing Android notification experience.
- Protect confidentiality, integrity, peer identity, and replay state over an
  actively attacked Bluetooth transport.
- Provide per-user installation, exact-origin host registration, upgrade,
  uninstall, development registration, and actionable missing or incompatible
  host recovery on Windows and macOS.
- Keep the watch pairing ceremony identical to the desktop ceremony from the
  phone's point of view.

## Permanent privacy constraints

The following are prohibited product directions, not merely deferred features:

- Cloud relay or internet transport of OTP data.
- User accounts or account-backed product features of any kind.
- Cross-device synchronization through a third-party or Veles-operated service.
- Telemetry, analytics, remote diagnostics, or remote crash reporting of any
  kind, whether or not the payload is intended to be sanitized.
- Remote executable code or runtime-downloaded cryptographic code in the
  Android application or Chrome extension.

OTP data may move only between a user's Veles Android installation and locally
connected Veles client installations (the Chrome extension or a paired watch).
Chrome Web Store distribution and ordinary application update delivery do not
change this data boundary.

## Non-goals

- Durable OTP history on Android or any client.
- Trust based on a Bluetooth address, Bluetooth name, OS bond, or Android
  companion association.
- Hiding BLE service UUIDs, connection timing, traffic timing, or encrypted
  record sizes.
- Protecting data after compromise of an unlocked phone, Chrome profile,
  browser, operating system, extension context, screen, or clipboard.
- A persistent native daemon, login item, background agent, or native-owned
  product UI.
- Treating an extension-stored platform device hint, Bluetooth address, name,
  or OS pairing state as identity or authorization.
- Chrome or operating-system OTP notifications in the initial release.
- Native storage of Chrome trust, cryptographic session state, OTP history,
  settings, or telemetry.
- Guaranteed concurrent operation of multiple Chrome profiles through one
  physical Bluetooth adapter.

## Product behavior

### First pairing

1. The user installs Veles Android and grants its notification and Nearby
   Devices permissions.
2. The user installs Veles Chrome from the Chrome Web Store.
3. The user enables Desktop Sharing on Android. Android starts its foreground
   Bluetooth service and advertises the public Veles service.
4. The user starts Add device from the unlocked phone. Android opens one pending
   pairing window and displays a random, six-digit, five-minute code.
5. The user selects the extension action. Chrome opens or focuses the connector
   tab and attempts to start the registered bridge through Native Messaging.
   (On the watch, the user instead opens the Veles watch app and selects "Pair
   with phone"; the watch scans for phones advertising the public Veles service
   and lists them by name and signal strength.)
6. If the connector reports that the required compatible host is missing, the
   user installs the per-user Veles Native Bridge package and selects Retry.
   (Desktop only; the watch needs no host.)
7. The user selects Find phones. The connector renders bridge discovery events
   and the user selects the intended advertising phone. On the watch, the user
   taps the phone from the scan list. OS Bluetooth pairing is not a setup step
   on either client type.
8. The user enters the code in the connector tab. On the watch, the user
   transcribes the code from the phone onto an on-screen digit dial.
9. Android and the client complete SPAKE2 and its bidirectional key
   confirmation, exchange installation identities through the protected
   enrollment channel (including the client's `device_type`: `desktop` or
   `watch`), and activate trust through prepare/commit/ack.
   The watch then offers optional local-PIN setup (digits, minimum four): the
   user may set a PIN (entered twice to confirm) or skip, leaving the OTP
   display ungated by default; the PIN is stored only on the watch and never
   transmitted.
10. Both endpoints erase the code, pending SPAKE2 state, and enrollment traffic
    keys. Pairing ends with both devices displaying the same enrollment
    fingerprint for visual comparison.

### Later use

1. The user selects the extension action. Chrome opens or focuses the connector
   tab.
2. The connector opens the Native Messaging port. For each remembered phone it
   asks the bridge to connect using a locally stored platform hint or starts a
   bounded scan when the hint is absent or stale.
3. A hint only narrows OS discovery. Chrome accepts no phone identity or data
   until the stored Veles identity authenticates a fresh protected session.
4. Phones that cannot reconnect automatically appear in extension-owned search
   UI for explicit selection; no pairing code is required unless trust was
   revoked or lost.
5. Chrome requests eligible OTP events from the preceding ten minutes.
6. The user may background the connector tab and work in another tab.
7. Android pushes eligible new OTP events while the connector remains open and
   connected.
8. Chrome updates its history and badge and, when enabled, requests automatic
   copy through the offscreen clipboard helper.

Closing the connector tab closes its Native Messaging port, disconnects every
BLE link, erases every live cryptographic session, and lets the on-demand host
exit. Reopening uses saved hints for authenticated automatic reconnect where
possible and otherwise offers search and explicit selection. It does not
require new SPAKE2 enrollment unless trust was revoked or lost.

### Revocation and recovery

- Either endpoint can delete one peer trust record without affecting other
  peers.
- Android immediately disconnects sessions belonging to a revoked client
  installation; a revoked watch learns this at its next reconnect and shows
  "pairing revoked — re-pair required."
- A connected peer may request mutual removal. Offline removal is local; the UI
  explains that the other endpoint may retain its local record until it is
  separately removed.
- A watch's forgotten local PIN has no recovery path: the watch erases its
  trust store (peer record, session state, PIN verifier) and re-pairs from the
  beginning. Phone-side revocation of the stale watch record is recommended
  but not required for the reset to be safe. Changing the watch PIN is local
  and needs no re-pairing.
- Reinstallation, extension removal, app-data loss, profile loss, private-key
  loss, or trust-store corruption fails closed and requires pairing again.
- A missing, incompatible, or unregistered native host leaves trust records
  intact, exposes no OTP data, and presents installer or repair actions. A
  stale platform hint is discarded after bounded failure and falls back to
  search, never to unauthenticated trust.
- Uninstalling the extension does not uninstall the per-user host; uninstalling
  the host removes its manifest registration and binaries without modifying the
  Chrome profile. Reinstalling either component resumes only if Chrome's key and
  peer record remain usable.
- A pairing attempt cannot replace an existing identity under the same
  installation ID. Replacement requires explicit revocation first.

## Functional requirements

### OTP model and retention

An OTP event contains:

- Phone installation ID and event ID used together for deduplication.
- Receipt time and remaining lifetime.
- OTP code.
- Merchant.
- Amount and currency.

Android owns event identity, eligibility, and expiry. It retains at most five
events globally for no more than ten minutes in process memory. The buffer
exists only while Desktop Sharing is enabled and is cleared when sharing is
disabled, the process dies, or the phone reboots.

Chrome retains at most five events globally across all phones for no more than
ten minutes in browser-session storage. Android sends remaining lifetime so
Chrome cannot extend retention because of clock skew. Chrome merges repeated
events by phone installation ID and event ID. Browser or profile shutdown,
session-storage loss, and expiry clear the history.

The watch mirrors the global rule with a stricter storage posture: at most five
events, all in RAM, expiry from the phone's authoritative remaining lifetime
(the watch never extends retention from its own clock), and the buffer clears
on reboot and pairing reset. On reconnect after absence, the watch may issue
one pull of recent events to backfill anything pushed while disconnected.

### Delivery policy

Android evaluates the selected policy immediately before every pull response
and push:

- **Unlocked only**, default: the phone must be interactive and unlocked.
- **Display on**: the phone must be interactive, including at the lock screen.
- **Always**: delivery is allowed regardless of display or lock state.

An event received while delivery is blocked remains in the memory buffer until
normal expiry. It may be pulled after the phone becomes eligible. A blocked
authenticated pull receives a protected phone-state response rather than an
empty-history response.

### Copy behavior

Desktop-only behavior (the watch displays OTPs but has no clipboard
integration; it never offers copy):

- Manual copy is initiated from focused extension UI.
- Automatic copy is a global setting and is disabled by default.
- A successful pull copies the newest event returned, including a repeated
  pull, when automatic copy is enabled.
- A newly pushed event is copied when automatic copy is enabled.
- Background automatic copy is sent through a service-worker-coordinated
  offscreen document.
- The offscreen document receives only the immediate copy request. It never owns
  Bluetooth, trust state, traffic keys, session history, or durable OTP state.
- Copy failure is visible and does not terminate the protected session.

### Multiple peers

The stable release must support:

- One Android phone serving at least two physical desktop clients concurrently
  against stable Chrome on Windows and macOS (the GATT service caps at eight
  simultaneous clients). Watch clients are exercised only in the virtual
  harness; physical watch validation is deferred to a follow-up RFC after
  OTP-26/OTP-27 (see Rejected and deferred alternatives).
- One connector tab and its single bridge process serving multiple Android
  phones through independent connections.

Every peer has an independent installation identity, trust record, protected
session, sequence space, delivery queue, revocation state, and failure
boundary. A failed or slow peer cannot block local Android behavior or another
peer.

## Architecture

### Existing Android OTP path

The current Android matching and replacement-notification path remains intact:

```text
Incoming notification
  -> NotificationListener
  -> bank handler chain
  -> matched OtpMessage
  -> existing local Veles notification and copy behavior
```

Desktop delivery is an additional consumer of a matched `OtpMessage` only
after the protected synthetic validation gate passes. Bluetooth startup,
delivery, or copy failure must never suppress or delay the local Veles
notification.

### Android components

#### Desktop delivery coordinator

The coordinator accepts matched OTP events, assigns event IDs, writes eligible
events to the memory buffer, and fans them out to eligible protected sessions.
It contains no Android Bluetooth API and isolates notification matching from
the foreground-service lifecycle.

#### Memory-only OTP buffer

The buffer enforces the global five-event and ten-minute limits, uses monotonic
time for runtime expiry, and exposes only currently eligible snapshots. It
never writes OTP data to a database, preferences, logs, backups, or saved UI
state.

#### Desktop-sharing service

A user-enabled `connectedDevice` foreground service owns:

- Bluetooth capability and permission state.
- Plain-GATT advertising and server lifecycle.
- Bounded fragmentation and reassembly.
- Per-connection transport queues.
- SPAKE2 enrollment sessions and protected normal sessions.
- Multi-client fan-out and session cleanup.
- Actual availability, advertising, and connection state for the Android UI.

The service starts from visible user action. The enabled preference persists,
and restoration after ordinary process death, reboot, package replacement, or
Bluetooth recovery is best effort where Android permits it. Permission loss,
unsupported peripheral advertising, OEM restriction, and Android Task Manager
Stop are explicit unavailable states.

Plain GATT reveals no OTP, trust record, meaningful protected error, or
production application command before Veles authentication. Bluetooth
addresses are connection hints only.

Per-peer delivery queues hold bounded event references rather than independent
OTP copies. Android resolves each reference from the authoritative memory
buffer and rechecks expiry and delivery policy immediately before creating a
protected record. Evicted, expired, or newly blocked events are dropped before
encryption. Only the currently transmitting protected record may temporarily
exist outside the buffer, and it is erased when that transfer completes or
fails.

#### Android trust store

Android uses a non-exportable Android Keystore key for its installation
identity. App-local storage holds peer IDs, peer public keys, labels,
fingerprints, creation time, last-seen time, and state required by the approved
protocol. Identity metadata and peer records are excluded from cloud backup and
device-to-device transfer.

#### Android user interface

The Home screen gains a Desktop Sharing card that opens a dedicated Connections
screen. No fourth bottom-navigation item is added. The screen provides:

- Sharing enablement and actual service state.
- Pair device action (Chrome today and the watch once its firmware lands) and
  pairing-code state.
- Delivery-policy selection.
- Trusted and currently connected client lists.
- Peer details and revocation.
- Actionable Bluetooth, permission, service, key-loss, and compatibility
  errors.

The foreground-service notification opens the Connections screen and reports
sharing and connection state without exposing OTP content.

### Chrome extension components

#### Project and build

The Manifest V3 extension is a TypeScript npm project under repository-root
`src/typescript`. Gradle provides pinned, reproducible entry points for npm,
TypeScript, extension tests, Rust, JNI, WASM, lint, package, cryptographic
fixtures, native-host and installer builds, checksums, signing inputs, and
software-bill-of-materials generation.

The extension packages WASM locally, declares only required CSP and
permissions, and downloads no runtime code.

#### Service worker

The MV3 service worker does not own the Native Messaging port, cryptographic
sessions, or OTP history. It:

- Creates a connector tab when none exists.
- Activates and focuses the existing connector tab on repeated action clicks.
- Updates the toolbar badge from connector messages.
- Creates and coordinates the clipboard-only offscreen document.

Extension-owned connector contexts are discovered through supported extension
APIs rather than broad host access or unnecessary tab permissions.

#### Connector tab

The connector tab owns:

- The Native Messaging port and compatibility handshake.
- Extension-owned scan results, explicit phone selection, platform hints, and
  per-phone native handles.
- SPAKE2 pairing and protected normal sessions.
- Pull, push, acknowledgement, and liveness state.
- Short-lived rendered history and per-phone connection state.
- Multiple independent phone connections.

One connector owns one port and one bridge process. It correlates every request
by request ID and keeps each phone's native handle, protected session, queue,
and failure boundary independent. The tab must remain useful when backgrounded;
BLE notifications arrive as native-host events and liveness must not depend on a
five-second or similarly short throttled JavaScript interval.

#### Native bridge

Chrome starts the headless Rust and Tauri bridge only through
`runtime.connectNative`. The bridge uses WinRT Bluetooth APIs on Windows and
CoreBluetooth on macOS. It owns scanning, OS BLE identifiers, GATT discovery,
subscriptions, bounded byte transport, and disconnect cleanup. It has no
window, login item, listener socket, autostart entry, updater daemon, durable
database, product trust decision, cryptographic protocol implementation, or OTP
interpretation. Closing the Native Messaging port or receiving `shutdown`
stops scans, disconnects all handles, clears process memory, and exits.

The bridge may hold only bounded ephemeral scan, request, handle, connection,
queue, fragment, and reassembly state plus in-flight opaque record bytes. It
clears request and payload memory on operation completion, per-connection state
on disconnect, and all remaining process memory on stdin EOF, port closure, or
process exit. It persists no payload, trust record, key, identity, hint, queue,
or connection state and never parses OTP or authenticated Veles record fields.

Native Messaging uses length-prefixed UTF-8 JSON only for the local bridge
control envelope. The pre-negotiation envelope is immutable
`envelope_version: 1`. A `hello` includes that envelope version, its `type`, a
connector-generated unique `request_id`, and the extension's supported bridge
API versions or inclusive range. The host selects the highest mutually supported
version and returns `ready` with `api_version` and capabilities. Before
negotiation completes, `hello` is the only valid request and `ready` or a
correlated `error` are the only valid host responses. If no overlap exists, the
host returns correlated code `incompatible_api` under envelope version 1,
flushes that error to stdout, and exits. After `ready`, every command and event
contains both `envelope_version: 1` and the selected `api_version`.

At the connector API boundary, every request terminates in exactly one
correlated operation-specific success event or correlated `error`. The host
emits that terminal event while stdout is available; if the native port fails,
the connector synthesizes one correlated `error` for each outstanding request.
Unsolicited lifecycle and data events have no `request_id`; they carry only the
relevant scan or connection handle.

The envelope and selected bridge API contract define these requests:

| Request | Required fields and behavior |
|---|---|
| `hello` | Request ID plus the extension's supported bridge API versions or inclusive range under envelope version 1; negotiates before any BLE operation. |
| `start_scan` | Veles service UUID; starts a bounded scan and returns its process-local scan handle in `scan_state`. |
| `stop_scan` | Scan handle; stops that scan without affecting unrelated connections. |
| `connect` | A current discovery handle or non-authoritative platform hint; creates one process-local connection handle. |
| `disconnect` | Connection handle; disconnects and releases only that phone. |
| `send` | Connection handle and exactly one `record_base64` field containing one complete bounded opaque Veles protocol record; the bridge exclusively decodes, fragments, and writes it to GATT. |
| `shutdown` | Stops every scan and connection and exits after reporting completion. |

The envelope and selected bridge API contract define these events:

| Event | Required fields and behavior |
|---|---|
| `ready` | Correlated success for `hello` only under envelope version 1; reports the selected `api_version`, bridge version, platform, and capabilities. |
| `device_found` | Unsolicited scan event with scan handle, process-local device handle, display label, optional platform hint, and bounded advertisement metadata. |
| `scan_state` | Correlated `started` success for `start_scan`, including its scan handle, or `stopped` success for `stop_scan`; later unsolicited scan interruption has no request ID. |
| `connected` | Correlated `connect` success after GATT discovery and subscription, with a process-local connection handle and refreshed optional platform hint. |
| `disconnected` | Correlated `disconnect` success after that handle is released, or an unsolicited link-loss event without a request ID; affects only that connection. |
| `sent` | Correlated `send` success after the complete opaque record has been accepted, base64-decoded, fragmented, and every fragment has been successfully written to GATT. It does not mean Android application acknowledgement. |
| `message` | Unsolicited connection event with exactly one `record_base64` field containing one complete byte-for-byte reassembled opaque record; the bridge does not parse the Veles record. |
| `shutdown_complete` | Correlated `shutdown` success emitted after all scans and connections are closed and immediately before normal process exit. |
| `error` | Correlated request failure with bounded code, recoverability, and safe stage, or an unsolicited host failure without a request ID; pre-`ready` `incompatible_api` carries only envelope version 1, while post-`ready` errors also carry the selected API version; contains no sensitive payload. |

The connector/bridge boundary always carries complete opaque records. Within
the desktop stack, the bridge exclusively owns BLE fragmentation and
reassembly; the connector never creates, receives, or interprets a BLE
fragment. Base64 encode/decode preserves the exact record bytes and is only a
JSON-envelope transport encoding; it is not the authenticated Veles record
schema. The connector and bridge must not serialize, inspect, or remap any
authenticated Veles record field as JSON.

The stdout event sequence is authoritative. A correlated `scan_state: started`
precedes `device_found` for that scan, and `scan_state: stopped` follows its last
device event. `connected` precedes every `message` or unsolicited `disconnected`
for its handle. A requested disconnect first terminates affected in-flight
requests with correlated `error`, then emits one correlated `disconnected` and
no separate lifecycle event. On asynchronous link loss, the bridge first emits
correlated `error` for each affected in-flight request, then exactly one
unsolicited `disconnected`; no later `sent` or `message` may use that handle. A
`sent` emitted before a later disconnect confirms only completed GATT writes.
`shutdown` terminates every other pending request with correlated `error`, emits
any final unsolicited scan or connection lifecycle events without request IDs,
then emits `shutdown_complete` as the last JSON message before stdout closes.

Request IDs disambiguate concurrent operations. Device, scan, and connection
handles are random or opaque, valid only in the current bridge process, and
never represent Veles identity. These identifiers and independent operation
state are required so one bridge process can serve multiple phones without
cross-routing bytes or failures. Unknown envelope versions, pre-`ready`
messages other than `hello`, mismatched post-negotiation API versions, unknown
message types, request IDs, handles, duplicate terminal responses, malformed
JSON, invalid base64, and oversized fields fail closed within explicit
per-request and process limits.

#### Native bridge installation

The production Native Messaging host name is `me.nagaev.veles.bridge`. The
macOS bundle identifier is also `me.nagaev.veles.bridge`. Before packaging,
the build requires the immutable Chrome Web Store item ID from signed release
configuration and writes its one literal Chrome extension origin into
`allowed_origins`. Wildcards, missing IDs, development IDs, and unrelated
extension origins fail the build.

- On Windows, a per-user signed installer writes versioned bridge files under
  `%LOCALAPPDATA%\Veles\NativeBridge`, writes the host manifest there with an
  absolute executable path, and registers its manifest path in
  `HKCU\Software\Google\Chrome\NativeMessagingHosts\me.nagaev.veles.bridge`.
  It requires no administrator access, service, scheduled task, or machine-wide
  registry entry.
- On macOS, a per-user signed, notarized, and stapled installer writes versioned
  bridge files under `~/Library/Application Support/Veles/NativeBridge` and
  places the host manifest at
  `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/me.nagaev.veles.bridge.json`
  with an absolute executable path. It installs no launch agent or daemon.
- Upgrade stages and verifies the complete new signed payload before atomically
  replacing the active version and manifest. Failure preserves the last valid
  installation. Uninstall removes the per-user registration, manifest, and all
  bridge files, leaves no running host after its ports close, and never reads or
  modifies extension trust or OTP storage.
- Development uses host name `me.nagaev.veles.bridge.dev`, a separate
  manifest and install root, and an explicitly supplied unpacked-extension
  origin. Repository scripts register and remove it per user; production
  installers never authorize a development origin.

On connector startup, missing registration, an unlaunchable executable, early
process exit, and broken stdin/stdout are reported by `runtime.connectNative` or
native-port errors before any `hello` response. Only a successfully launched
host can answer `hello`; `ready` then reports API compatibility and
capabilities, while a correlated `error` reports incompatibility. The connector
offers signed installer/repair instructions and retry without deleting trust.
The extension and host update independently. Any overlapping API versions
negotiate successfully; no overlap fails closed through `incompatible_api` and
visible host or extension install/update guidance. Each release supports only
its explicitly tested compatibility window and does not promise indefinite
backward compatibility.

Independent clean builds produce byte-identical unsigned bridge binaries,
unsigned bundle/package payloads, host manifests, and installer inputs. Signed
outputs are not required to be byte-identical because signatures, timestamps,
notarization, and stapling may vary. Release verification instead checks
signature and notarization validity, signing provenance, content correspondence
to the reproducible unsigned inputs, recorded release hashes, checksums,
licenses, and SBOMs.

#### Clipboard helper

The offscreen document exists only when required for a copy operation and is
destroyed or made idle promptly afterward. Requests are validated as coming
from the trusted connector/service-worker flow. The helper does not persist or
render OTP history.

#### Chrome trust and session storage

Chrome generates a random 128-bit installation ID and non-extractable P-256
identity key. The private `CryptoKey` lives in IndexedDB. Peer public records
live in local, non-sync extension storage restricted to trusted extension
contexts. Content scripts cannot access either store.

OTP events use session storage only. Trust metadata, settings, and the latest
successful OS platform hint for each peer may persist; OTP data may not. A hint
is used only as a connection candidate, is replaced after an authenticated
connection, and cannot select a trust record, satisfy authentication, or cross
Chrome profiles.

## Data flow

### Pull

```text
Connector opens Native Messaging port and completes hello
  -> connector chooses direct connect by hint or issues start_scan
  -> bridge executes the requested operation and emits results
  -> connector selects a result when needed and issues connect
  -> bridge opens GATT and emits a process-local connection handle
  -> mutually authenticated protected session
  -> Chrome sends encrypted pull request
  -> connector passes one complete opaque record through send
  -> bridge fragments and writes it, then emits sent
  -> Android checks delivery policy and expiry
  -> bridge reassembles one complete record and emits message
  -> Chrome verifies, decrypts, merges, and updates badge
  -> optional auto-copy sends newest code to clipboard helper
```

### Push

```text
Android matches a new OTP
  -> existing local Veles behavior continues
  -> coordinator adds event to memory buffer
  -> Android checks delivery policy for each protected session
  -> Android queues an event reference independently per eligible peer
  -> immediately before send, Android rechecks policy/expiry and encrypts
  -> bridge reassembles and emits one complete opaque record for the handle
  -> each Chrome session verifies, decrypts, displays, and acknowledges
  -> optional auto-copy sends the code to clipboard helper
```

Disconnection abandons pending delivery. A later connection derives fresh
keys and performs a fresh pull. The protocol does not persist an offline queue.

## Application security

### Threat model

Plain GATT is treated as an actively attacked channel. The protocol protects
against passive capture, active man-in-the-middle, peer impersonation, message
modification, replay, reordering, duplication, cross-session records, and an
unknown nearby BLE client.

The user is assumed to read a short code from the intended unlocked phone and
enter it in the intended Veles connector tab. Online guesses are constrained
by expiry, attempt limits, and visible pairing state. A malicious or replaced
native host can observe or alter BLE timing and opaque bytes and cause denial of
service, but cannot decrypt or forge a protected session. Endpoint compromise,
including compromise of Chrome or the operating system, is outside this
transport threat boundary.

### Cryptographic implementation

First pairing uses balanced SPAKE2 from RFC 9382. A pinned Rust core is compiled
as an Android NDK library behind a narrow JNI interface and as locally packaged
browser WASM. Kotlin and TypeScript do not independently implement the PAKE.

The baseline ciphersuite is `SPAKE2-P256-SHA256-HKDF-HMAC`. Android is fixed as
party A and sends first; the client is fixed as party B (the Chrome desktop
client and the watch client alike). The RFC 9382 identity byte
strings included in `TT` are exactly `Veles Android SPAKE2 party A v1` for A and
`Veles client SPAKE2 party B v1` for B, encoded as ASCII. The core uses the RFC
9382 P-256 M and N points, uncompressed SEC1 point encoding, complete point
validation, fresh uniformly sampled scalars for every attempt, constant-time
secret-dependent group operations and comparisons, and the RFC 9382 `cA` and
`cB` confirmation messages. Neither endpoint treats SPAKE2 as complete or
releases enrollment data until it has verified the peer's confirmation.

The six ASCII digits, including leading zeroes, are transformed into `w` by
`prk = HKDF-Extract-SHA256(salt = pairing_id, IKM = code_ascii)` followed by
`okm = HKDF-Expand-SHA256(prk, info, 40)`, where `info` is exactly the ASCII
label `Veles SPAKE2 password input v1`. `w` is the 40-byte `okm` interpreted as
a big-endian integer modulo the P-256 subgroup order. The 320-bit intermediate
follows RFC 9382's guidance to avoid material reduction bias. The transform is
not an entropy amplifier: the security boundary remains one online guess per
protocol execution plus the pairing expiry and attempt limits.

No memory-hard preprocessing is used. The code is uniformly random, one-use,
held only during a five-minute pairing window, and has no durable verifier;
SPAKE2 prevents a captured transcript from validating guesses offline. A
memory-hard step would not change the unavoidable online-guessing bound and
would add browser/mobile latency and a nearby denial-of-service surface. Any
change to this decision requires protocol and external security review.

The core pins the ciphersuite, dependency graph, role assignment, password
encoding, context, serialization, bounds, and errors. Performance is measured
before release; an unreviewed runtime downgrade is prohibited.

Normal sessions use platform cryptography:

- P-256 ECDSA persistent installation identities.
- Fresh P-256 ECDH keys for every connection.
- HKDF-SHA-256 directional key separation.
- AES-256-GCM application records.
- SHA-256 transcripts and fingerprints.
- Platform cryptographically secure randomness.

Private identity keys remain in Android Keystore or non-extractable Web Crypto
storage and never pass through Rust or WASM.

### SPAKE2 enrollment

Android creates one random 128-bit pairing ID and one uniformly random six-digit
code, preserving leading zeroes. Pending state and code expiry use monotonic
time. Before SPAKE2, both endpoints construct one canonical, length-prefixed
public context containing the application and protocol version, exact
ciphersuite, pairing ID, fixed A/B roles, both fresh pairing nonces, and both
offered capability sets in role order. This context is supplied identically as
RFC 9382 AAD and is bound into key confirmation. Stable installation IDs and
identity public keys are not sent before SPAKE2 confirmation.

One pairing ID permits at most five online attempts. Android permits only one
SPAKE2 operation in flight and no more than five attempts in any rolling
five-minute interval across pairing IDs. After validating framing, pairing ID,
length, and the received `pB` point, Android atomically and non-refundably
consumes one attempt before any password-dependent group operation or `cA`
response. A duplicate or replayed valid `pB`, later confirmation failure,
disconnect, cancellation, or timeout still consumes that attempt. Malformed or
invalid-point input is rejected before attempt consumption but remains subject
to separate framing, request-rate, concurrency, and resource limits. Excess
valid attempts are rejected before password-dependent group operations begin.
Android also permits only one active pairing window. Unknown, expired,
incorrect, rate-limited, busy, and malformed attempts produce generic failures.

After both RFC 9382 confirmation messages pass, enrollment derives
`enrollment_prk = HKDF-Extract-SHA256(salt, Ke)`, where `salt` is
`SHA-256(label || len(AAD) || AAD)` and `label` is exactly the ASCII string
`Veles SPAKE2 enrollment salt v1`; `len(AAD)` is the unsigned 64-bit
little-endian byte length used by RFC 9382's `len` function. HKDF-Expand derives
32-byte AES keys, four-byte nonce prefixes, and 32-byte HMAC-SHA-256 activation
confirmation keys independently for each direction under the exact ASCII labels
`Veles enrollment A-to-B AES key v1`, `Veles enrollment A-to-B nonce v1`,
`Veles enrollment A confirmation v1`, `Veles enrollment B-to-A AES key v1`,
`Veles enrollment B-to-A nonce v1`, and
`Veles enrollment B confirmation v1`. Enrollment nonces concatenate the
directional four-byte prefix with an unsigned 64-bit big-endian sequence number
starting at zero. The complete `Hash(TT)` is not an input because its `Ka` half
is reserved exclusively for RFC 9382 key confirmation; `Ke` is never used
directly. Android sends the first protected enrollment record only after it
verifies `cB`; Chrome releases its protected enrollment record only after it
authenticates that Android record, proving Android reached confirmation. The
records contain installation ID, label, `device_type` (required: `desktop` or
`watch`), identity public key, fingerprint, and nonce.

Trust activation is explicitly interruption-safe rather than distributed-
atomic. Both endpoints first persist inactive `pending` records and confirm the
canonical enrollment transcript, which binds both installation records, the
complete SPAKE2 exchange, and activation state. Chrome then sends `commit`;
Android atomically transitions to `committed_waiting_ack` and sends
`activation_complete`; Chrome atomically transitions to `active` and sends
`activation_ack`; Android transitions to `active` only after authenticating that
ack. Pending or `committed_waiting_ack` records authorize only this bounded,
idempotent activation-recovery exchange and never OTP pull, push, or other
application records. If any message is lost, a later authenticated recovery
connection resumes the recorded state. Android sends no OTP until it is active,
which proves Chrome reached active and sent the final ack. Incomplete records
expire automatically or are removed by explicit cancellation or revocation.

### Authenticated reconnect

After native connection and GATT setup, each peer sends a versioned Veles hello
with its installation ID, fresh ECDH public key, fresh 128-bit nonce, role,
version, and capabilities. This encrypted-transport protocol hello is distinct
from the local Native Messaging `hello` request. Both peers sign one canonical
transcript with their stored P-256 identity keys.

ECDSA signatures use strict, fixed-width, 64-byte P1363 `r || s` encoding and
low-S normalization. Neither side authenticates the session before both
signatures and encrypted key confirmations pass.

HKDF derives independent client-to-phone and phone-to-client AES keys, nonce
prefixes, a session ID, and direction-specific confirmation values. Ephemeral
keys, traffic keys, prefixes, IDs, and counters are fresh on every connection.

### Encoding and protected records

The authenticated Veles record schema uses canonical binary encoding,
network-byte-order integers, explicit lengths, strict UTF-8, and fixed bounds;
it is never serialized, inspected, or remapped as JSON. Native Messaging JSON
may carry only the complete opaque binary record in `record_base64`, whose
base64 decode must reproduce the exact authenticated bytes and does not become
part of the authenticated schema. Unknown values, duplicate fields, trailing
bytes, non-canonical lengths, and malformed key encodings are rejected before
state mutation.

Every post-handshake command, response, push, pull, heartbeat, acknowledgement,
and error is an AES-256-GCM record. Associated data binds protocol version,
schema version, session ID, direction, sequence number, record type, and
ciphertext length.

Each direction accepts exactly its next unsigned sequence number. Duplicate,
skipped, reordered, unauthenticated, oversized, unknown-type, or cross-session
records terminate the session and erase traffic keys. Counters never wrap and
are never reused after reconnect.

BLE fragmentation is transport-only. The Chrome connector submits and receives
only complete records; the native bridge exclusively fragments outbound records
and reassembles inbound records on the desktop side. Fragment IDs are not
cryptographic sequence numbers. Reassembly, peer, queue, message, and process
limits are explicit and tested. The baseline security design caps the service
at eight simultaneous clients, two incomplete messages and 8 KiB reassembly
per peer,
16 incomplete messages and 64 KiB globally, 16 queued messages and 16 KiB per
peer, and 128 queued messages and 128 KiB globally.

### Safe observability

Logs and status surfaces may contain a protocol stage, bounded non-secret error
code, and timing. They must not contain OTPs, pairing codes, keys, public-key
encodings, plaintext records, trust secrets, cryptographic intermediates, or
raw ciphertext. Native-host logs are disabled in production except for an
explicit local diagnostic mode that follows the same restrictions and never
logs Native Messaging `send` or `message` bodies. No remote telemetry exists.

### Watch PIN policy

A watch may gate OTP display behind a local PIN. The PIN is **optional and off
by default**, **never transmitted, never escrowed, absent from every protocol
message, and unknown to the phone**. Pairing trust is unchanged by enabling,
changing, or disabling the PIN. When enabled, the user sets the PIN on the
watch immediately after pairing completes (twice, to confirm) — or skips,
leaving the display ungated — and may change it later by entering the current
PIN first. Wrong-PIN entry uses an exponential lockout — 1 s, 2 s, 4 s, 8 s,
16 s, capped at 32 s between attempts — persisted in flash so a reboot does not
reset it; the counter resets after the first successful entry. There is no
hard lockout-to-reset: with only an offline verifier and the exponential
backoff, brute force against a local PIN is slow enough, and a physical reset
remains available regardless (see Revocation and recovery: a forgotten or
unrecoverable PIN requires erasing the trust store and re-pairing). A hardware
reflash can always bypass the verifier — accepted risk for a watch-class
device; the PIN is a display gate, not a trust boundary.

## Sensitive-notification access

Android sensitive-notification companion association remains an independent,
permission-only workaround. It may use any suitable nearby device and is not
part of Desktop Sharing setup. Because both this feature and watch pairing may
involve a watch-class device over Companion Device Manager: the companion
association (including a `DEVICE_PROFILE_WATCH` association) grants only the
sensitive-notifications role and never authorizes OTP delivery; Veles watch
trust is established exclusively by the SPAKE2 application-layer enrollment.

The Chrome connector does not require, reuse, infer, or modify companion
association. Companion association never authorizes OTP delivery. Veles Chrome
trust exists only after SPAKE2 enrollment.

## Failure handling

- Bluetooth disabled, missing Nearby Devices permission, unsupported
  advertising, service startup failure, and connection-capacity limits have
  distinct Android states.
- Chrome distinguishes missing host, native launch failure, incompatible host
  protocol, unsupported platform BLE, denied Bluetooth access, cancelled or
  empty search, unavailable phone, stale platform hint, stale Windows bond,
  policy denial, authentication failure, empty history, native-port closure,
  clipboard failure, and disconnect.
- Windows stale-bond guidance is troubleshooting only. Normal setup does not
  create or require an OS bond.
- Invalid, unknown, revoked, malformed, replayed, reordered, oversized, or
  incompatible protocol input returns no OTP data and fails closed.
- A per-peer failure removes or resets only that peer's session and queue.
- Android process death clears OTP history and live sessions. Chrome session
  loss or native-port closure clears OTP history and live sessions. Persisted
  trust survives only when its corresponding private key remains usable; native
  handles never survive host exit.
- A host crash, malformed host event, or mismatched connection handle tears down
  only affected operations where routing remains unambiguous, otherwise closes
  the port and every native connection. Recovery starts a new process and new
  protected sessions; it never restores process-local handles or traffic keys.
- Background timer throttling is not accepted as normal disconnect behavior.
  Event-driven native delivery must be demonstrated under a backgrounded tab.
- No fallback may send production OTPs through plaintext messages, the spike's
  public HMAC key, an unprotected debug protocol, or a downgraded ciphersuite.

## Validation and release gates

### Gate A: protocol and toolchain foundation

Before protected transport implementation is accepted:

- TypeScript, Rust, Android JNI, and browser WASM builds are pinned and
  reproducible.
- The native bridge control schema, supported OS API surface, unsigned package
  layout and payloads, exact-origin registration, signing inputs, and
  update/uninstall behavior are pinned; unsigned build outputs are byte
  reproducible.
- The protocol profile, binary schemas, transcript encoding, bounds, errors,
  and version negotiation are frozen and independently reviewed.
- Shared deterministic vectors run in Rust, Kotlin/JNI, and browser WASM.

### Gate B: protected synthetic transport

Before any task integrates real `OtpMessage` data:

- OTP-23 proves Native Messaging launch, scanning, independent multi-phone GATT
  transport, background event delivery, and cleanup with synthetic bytes on
  stable Windows and macOS.
- The production bridge and per-user installer path, not a development-only
  registration or Web Bluetooth fallback, is used by the matrix.
- SPAKE2 enrollment, trust activation, authenticated reconnect, protected
  records, revocation, and key-loss behavior pass cross-runtime tests.
- A synthetic-data physical matrix passes on stable Windows and macOS Chrome.
- The connector and Native Messaging port remain useful while backgrounded for
  at least ten continuous minutes with another ordinary tab focused and no
  audio, WebRTC, or any other timer-throttling exemption active, with a
  protected native-host event and offscreen copy after minute seven.
- Background copy succeeds through the offscreen helper.
- The Android foreground service passes the documented 20-minute
  task-removal, screen-lock, reconnect, pull, and push scenario: schedule a
  synthetic push for minute 20, background and remove the Android task, lock
  the phone for at least 15 minutes, confirm the foreground indication,
  connect and pull after minute 15, and remain connected until the scheduled
  push arrives at minute 20 within a recorded one-minute tolerance.
- Connector closure stops the host and all BLE links; reopening performs
  authenticated automatic reconnect by hint or extension-owned reselection and
  establishes a fresh protected session.
- Exact commits, hardware, OS builds, Chrome versions, adapters, timings, and
  outcomes are recorded.

The product owner's transport-go assumption does not replace Gate B. Gate B
validates the production security and lifecycle path rather than the original
synthetic HMAC spike.

### Gate C: real-data feature validation

After Gate B, automated and physical tests cover:

- Buffer capacity, expiry, deduplication, and delivery policies.
- Protected pull, push, acknowledgement, and reconnect.
- Multiple phones and multiple computers with independent failures.
- Android and Chrome process lifecycle, Bluetooth interruption, permission
  loss, host crash/upgrade/removal, native-port closure, stale hints,
  revocation, and storage loss.
- Manual and automatic copy semantics and visible failures.
- Binary decoder, fragmentation, state-machine, JNI, and WASM fuzzing and
  property tests.
- Proof that no sensitive value enters logs, durable OTP storage, backups,
  sync, content scripts, or release artifacts.

OTP-20 owns the Gate C exit decision. It cannot close until the complete Gate
C evidence is linked and the release owner records an explicit accepted or
rejected decision. An incomplete or inconclusive matrix leaves Gate C open.

### Gate D: release candidate

Before public release:

- An external reviewer assesses the exact threat model, SPAKE2 profile,
  password-to-scalar transform, Rust core, dependencies, schemas, enrollment,
  identity storage, key schedule, nonce construction, replay policy,
  revocation, JNI, WASM, Native Messaging envelope/parser/state machine,
  request correlation and handle routing, bounds/queues/framing,
  WinRT/CoreBluetooth adapters, Tauri headless lifecycle, exact-origin host
  registration, installer install/repair/upgrade/rollback/uninstall behavior,
  and packaged artifacts.
- All findings are fixed, and affected release-candidate components are
  re-reviewed.
- SPAKE2 completes within one second on each endpoint on the representative
  minimum and median Android devices and stable Windows/macOS browsers selected
  in the release matrix. A miss returns the profile for a separate revision
  approved by the release owner and external security reviewer; an unreviewed
  runtime downgrade is prohibited.
- Independent clean builds produce byte-identical unsigned bridge binaries,
  unsigned bundle/package payloads, manifests, and installer inputs. APK ABI
  contents, extension CSP, permissions, locally packaged WASM, dependency
  licenses, unsigned hashes, and SBOM are verified.
- Stable Windows and macOS pass the complete production matrix.
- Windows bridge and installer outputs have verified signatures; macOS bridge
  and installer outputs have verified signatures, notarization, and stapling.
  Provenance, content correspondence to the reproducible unsigned inputs, and
  release hashes are recorded; signed outputs need not be byte-identical.
- Clean install, atomic upgrade, rollback on failed upgrade, uninstall,
  exact-origin enforcement, and fresh-user repair paths pass on both platforms.
- Independent extension/host update matrices prove compatible API overlap and
  fail-closed `incompatible_api` behavior with visible install/update guidance;
  release does not require indefinite backward compatibility.
- Chrome Web Store disclosures match actual permissions, local data handling,
  and the permanent no-cloud/no-telemetry policy.

No compatibility mode may bypass a gate. Public release also requires the user
documentation in OTP-22.

## Rollout

- Desktop Sharing is disabled by default and begins only from visible user
  action.
- Pairing and normal sessions initially use synthetic records until Gate B
  passes.
- Native bridge feasibility (OTP-23), production bridge implementation
  (OTP-24), connector integration (OTP-07), and installer delivery (OTP-25)
  precede the protected synthetic release gate.
- Real OTP integration begins only with OTP-13 after Gate B evidence is
  accepted.
- Development builds keep diagnostics synthetic and isolated from production
  OTP data.
- Production release remains blocked until Gates C and D and OTP-22 are
  complete.
- Disabling Desktop Sharing closes sessions, stops advertising, clears the OTP
  buffer, and retains trust records until explicit revocation. Closing the
  connector independently shuts down its on-demand bridge process.

## Rejected and deferred alternatives

### Rejected

- **Production Web Bluetooth:** the historical spike established useful GATT
  evidence, but chooser-mediated, tab-owned device objects prevent the approved
  automatic reconnect and installer-controlled support model. Web Bluetooth is
  not a production fallback.
- **Action-popup Bluetooth ownership:** Chrome does not present the required
  chooser from the MV3 action popup.
- **Service-worker Bluetooth ownership:** MV3 service workers cannot own the
  page-scoped Web Bluetooth objects required by this flow.
- **Authenticated BLE attributes:** Windows Chrome cannot reliably use
  MITM-gated GATT attributes in the tested flow.
- **OS pairing as Veles trust:** OS bonds are inconsistent across platforms and
  do not authenticate an application installation.
- **Companion association as Veles trust:** the association grants an Android
  permission role only.
- **Cloud, accounts, synchronization, and telemetry:** permanently prohibited
  by the OTP data boundary and security model.
- **Plaintext production fallback:** prohibited even when secure setup or
  protected sessions fail.
- **OPAQUE for one-time pairing:** RFC 9807 is an augmented client/server PAKE
  whose registration phase requires an authenticated confidential channel and
  whose principal benefit is protecting long-lived server-side password
  records. Android generates, displays, and erases this one-time random code and
  stores no durable verifier, so local pre-registration adds OPRF, envelope, and
  state complexity without improving this threat model.
- **Ephemeral ECDH plus a short authentication string:** ordinary ECDH is
  available through Android Keystore and Web Crypto, but a secure six-digit SAS
  flow requires a reviewed commitment construction to prevent adaptive key or
  nonce grinding, changes the approved display-then-enter ceremony, and shifts
  security onto a bespoke human-confirmation protocol. Ordinary ECDH with the
  digits used as a raw PSK, MAC key, or transcript hash is prohibited.

### Deferred to separate RFCs

- Desktop OTP notifications.
- Experimental hidden/offscreen Web Bluetooth ownership.
- CPace as the first-pairing PAKE. It fits the balanced-PAKE threat model but is
  still a changing CFRG Internet-Draft rather than a published RFC; reconsider
  only through a future protocol revision with final specification, test
  vectors, implementation review, and migration analysis.
- Any Linux implementation, packaging, validation, support, or user
  documentation; all are wholly deferred to a future RFC.
- Watch firmware beyond the protocol surface defined here. The T-WATCH-S3
  firmware architecture (`watch/` monorepo layout with `watch/core`,
  `watch/lilygo/hal`, `watch/lilygo/twatch-s3`, later `watch/lilygo/
  twatch-ultra`), its UI and PIN-display UX, additional watch execution tasks,
  and hardware validation (including battery life) are designed at the sketch
  level (see the watch-client appendix) but become binding only in a follow-up
  amendment or RFC after spikes OTP-26/OTP-27 land. This RFC commits only the
  protocol and the spikes.

## GitHub Project model

Create a public GitHub Projects v2 project under `raidenyn` and link it to
`raidenyn/veles-android`. Suggested title: **Veles OTP external devices**.

Use these fields:

| Field | Type | Values |
|---|---|---|
| Status | Single select | Backlog, Ready, In Progress, In Review, Blocked, Done |
| Implementation order | Number | 1 through 27 |
| Phase | Single select | Foundations, Security, Transport, Core delivery, Product UX, Hardening, Release |
| Area | Single select | Android, Chrome, Native bridge, Cross-platform, Documentation |
| Release scope | Single select | Stable release, Deferred |
| Gate | Single select | None, Blocks protected transport, Blocks real OTP, Blocks release |

Create these views:

- **Execution order:** table sorted by Implementation order.
- **Delivery board:** board grouped by Status and sorted by Implementation
  order.
- **Security gates:** table filtered to Gate other than None.
- **By phase:** table grouped by Phase and sorted by Implementation order.

Issue dependencies are authoritative. Numeric order is a stable topological
priority order, not a prohibition on parallel implementation after
prerequisites pass. See "Ordered execution tasks" for
the canonical order. Tasks may run in parallel where their explicit
dependencies allow it.

OTP-01 starts `Ready`. Every other issue starts `Backlog`, or `Blocked` when its
immediate prerequisite is actively unresolved. Move an issue to `Ready` only
when all dependencies and required gates are satisfied.

## Ordered execution tasks

The entries below are deliberately standalone and can be converted directly
into GitHub Issues. Stable task IDs remain valid before issue numbers exist.

### OTP-01: Add reproducible Chrome and cryptographic toolchains to Gradle

**Outcome:** The repository can deterministically install, test, build, and
package the TypeScript extension, pinned Rust JNI/WASM artifacts, and native
bridge release inputs through documented Gradle entry points.

**Scope:** Add the MV3 TypeScript npm project under `src/typescript`; add the
pinned Rust toolchain and wrapper build; produce Android JNI and local browser
WASM outputs; establish byte-reproducible unsigned Rust and Tauri native-host,
bundle/package payload, manifest, and per-user installer-input build entry
points for Windows and macOS; keep signing/notarization as a separate release
step; lock npm, Rust, NDK, WASM, and packaging dependencies; add format, lint,
type-check, unit-test, build, package, checksum, license, and SBOM commands.

**Exclusions:** No Bluetooth transport, cryptographic protocol behavior, UI, or
real OTP integration.

**Dependencies:** None.

**Acceptance criteria:** Independent clean documented environments produce
matching locked dependencies and byte-identical unsigned bridge binaries,
bundle/package payloads, host manifests, and installer inputs; Gradle exposes
CI-safe entry points; release artifacts contain only intended Android ABIs,
local extension code, and declared native-host packages; platform signing can
consume CI-provided credentials without embedding secrets; signed outputs are
verified for provenance and content correspondence but are not required to be
byte-identical; no runtime download or remote executable code is introduced.

**Verification evidence:** Independent clean Gradle and native build logs,
lockfiles, byte-for-byte unsigned artifact and installer-input hash comparison,
signed-to-unsigned content correspondence procedure, extension manifest/CSP
check, APK ABI inspection, and initial SBOM/license output.

**Project fields:** Implementation order 1; Phase Foundations; Area
Cross-platform; Release scope Stable release; Gate Blocks protected transport;
initial Status Ready.

### OTP-02: Freeze the protected Bluetooth protocol profile and schemas

**Outcome:** Android, Rust, and Chrome share one reviewed, versioned,
unambiguous protocol contract before protocol implementation expands.

**Scope:** Specify the RFC 9382 SPAKE2 profile, roles, password-to-scalar
transform, AAD and confirmation context, installation identities, enrollment
states, reconnect transcript, P1363 signature encoding, HKDF schedule, AES-GCM
records, nonce construction, sequence rules, binary schemas, fragmentation
bounds, protected pull/push/policy/acknowledgement records, pairing
attempt/concurrency limits, capabilities, errors, version negotiation, and
fixture format. Separately freeze immutable `envelope_version: 1`,
`hello`/`ready` bridge API negotiation, operation-specific completion events
including `sent`, unsolicited events, ordering, whole-record boundaries,
bounds, handles, errors, and the single `record_base64` JSON field. Prohibit
serializing, inspecting, or remapping authenticated Veles record fields as JSON;
base64 is only byte-preserving envelope transport. Record the threat model and
downgrade policy.

**Exclusions:** Production implementation and UI.

**Dependencies:** OTP-01.

**Acceptance criteria:** The protocol contains no placeholders or ambiguous
encodings; bounds and state transitions are explicit; under envelope version 1,
`hello` is the only pre-negotiation request and `ready` or correlated `error`
are the only responses; every other pre-negotiation message is rejected;
`ready` selects the highest overlapping API version; no overlap flushes
correlated `incompatible_api` and exits; post-`ready`
messages carry envelope version 1 and the selected API version; base64
round-trips exact binary record bytes without exposing authenticated fields as
JSON; deterministic vectors cover successful and rejected paths, including
compatible and incompatible extension/host versions; an independent
protocol/security review is accepted; implementation cannot negotiate
plaintext, the spike protocol, or unbounded backward compatibility.

**Verification evidence:** Versioned protocol document, machine-readable
envelope and binary-record fixtures, exact base64 byte-round-trip vectors,
pre/post-negotiation parser and state-machine checks, compatible/incompatible
extension-host version matrix, review record, schema/vector parser checks, and
cross-language vector test skeletons.

**Project fields:** Implementation order 2; Phase Security; Area
Cross-platform; Release scope Stable release; Gate Blocks protected transport;
initial Status Backlog.

### OTP-03: Implement and cross-test the pinned SPAKE2 core

**Outcome:** One reviewed Rust core performs first-pairing SPAKE2 operations
identically through Android JNI and browser WASM.

**Scope:** Implement or wrap the pinned RFC 9382
`SPAKE2-P256-SHA256-HKDF-HMAC` profile, fixed A/B roles, code-to-`w` transform,
input and state bounds, point validation, fresh scalar generation, bidirectional
key confirmation, serialization, zeroization where supported, narrow JNI and
WASM APIs, and stable error mapping.

**Exclusions:** Platform identity keys, trust-store activation, Bluetooth, and
real OTP records.

**Dependencies:** OTP-01 and OTP-02.

**Acceptance criteria:** RFC 9382 and project vectors pass in native Rust, JNI,
and WASM; wrong-code, invalid-point, malformed-element, expired-state, replay,
role/context mismatch, length, RNG failure, and boundary cases fail
consistently; core-owned generation and tests prove fresh scalar and state use
for every attempt; both confirmation messages are required; Kotlin and
TypeScript contain no independent PAKE implementation; browser packaging uses
only local WASM.

**Verification evidence:** Shared vector results from all runtimes, Rust tests,
JNI instrumentation tests, browser/WASM tests, fuzz results, dependency hashes,
and benchmark baseline.

**Project fields:** Implementation order 3; Phase Security; Area
Cross-platform; Release scope Stable release; Gate Blocks protected transport;
initial Status Backlog.

### OTP-04: Implement Android installation identity and trust storage

**Outcome:** Android has a non-exportable installation identity and a
fail-closed, backup-excluded store for pending and active Chrome trust records.

**Scope:** Generate the installation ID and Android Keystore P-256 ECDSA key;
store peer IDs, public keys, labels, fingerprints, lifecycle timestamps, and
pending/active state atomically; enforce backup and transfer exclusion; expose
testable repository interfaces; detect key loss and corruption.

**Exclusions:** SPAKE2 flow, GATT transport, pairing UI, and OTP storage.

**Dependencies:** OTP-01 and OTP-02.

**Acceptance criteria:** The private key is non-exportable; trust cannot be
activated without protocol confirmation; missing keys or corrupted records
fail closed; app-data clearing invalidates identity; backup/restore and device
transfer do not recreate usable trust; no secret is logged.

**Verification evidence:** Unit and instrumented tests, Keystore property
checks, backup/transfer exclusion tests, corruption and key-loss tests, and
safe-log assertions.

**Project fields:** Implementation order 4; Phase Security; Area Android;
Release scope Stable release; Gate Blocks protected transport; initial Status
Backlog.

### OTP-05: Implement Chrome installation identity and trust storage

**Outcome:** The extension has a non-extractable installation identity and a
local, non-sync, fail-closed store for pending and active phone trust records.

**Scope:** Generate the installation ID and non-extractable Web Crypto P-256
ECDSA key; persist the key in IndexedDB; keep peer metadata in restricted local
extension storage; implement atomic pending/active records, corruption
detection, identity loss handling, and per-peer non-authoritative platform
hints that never participate in trust selection.

**Exclusions:** SPAKE2 flow, native BLE transport, connector UI, sync storage,
and OTP history.

**Dependencies:** OTP-01 and OTP-02.

**Acceptance criteria:** The private key cannot be exported; it survives normal
browser restart but not extension/profile removal; peer records never sync;
content scripts cannot access identity or trust stores; missing keys and
corrupt state require re-pairing; platform hints can be replaced or deleted
without changing trust and are accepted only after authenticated pairing or
reconnect; no secret is logged.

**Verification evidence:** Browser tests, storage-access tests, restart and
removal tests, corruption/key-loss tests, content-script isolation tests, and
safe-log assertions.

**Project fields:** Implementation order 5; Phase Security; Area Chrome;
Release scope Stable release; Gate Blocks protected transport; initial Status
Backlog.

### OTP-06: Build the production Android plain-GATT sharing service

**Outcome:** Android can advertise and serve bounded synthetic protocol traffic
through a production-shaped foreground service without exposing real OTP data.

**Scope:** Add capability and permission checks, `connectedDevice` foreground
service lifecycle, plain-GATT service and characteristics, advertising,
fragmentation/reassembly, per-client queues, connection limits, cleanup,
availability state, and best-effort restoration.

**Exclusions:** SPAKE2, protected records, `OtpMessage` integration, and final
product UI.

**Dependencies:** OTP-01 and OTP-02.

**Acceptance criteria:** Release manifests request only required permissions;
the service starts from visible action; stop/disable closes sessions and
advertising; bounds match OTP-02; one failed client cannot block another;
unsupported or denied states are explicit; synthetic-only tests cannot access
production OTP data.

**Verification evidence:** Android unit and instrumented tests, manifest tests,
service lifecycle tests, queue and bounds tests, debug physical smoke test, and
release APK inspection.

**Project fields:** Implementation order 6; Phase Transport; Area Android;
Release scope Stable release; Gate Blocks protected transport; initial Status
Backlog.

### OTP-23: Validate Native Messaging BLE feasibility on Windows and macOS

**Outcome:** A disposable but production-shaped harness proves that stable
Chrome can drive the required OS BLE behavior through one on-demand native host
before the production bridge is built.

**Scope:** Build a Rust and Tauri windowless synthetic host and launch it from
stable Chrome through `runtime.connectNative` and exact-origin development
manifests; exercise the Native Messaging stdin/stdout framing and port
lifecycle, WinRT and CoreBluetooth scanning, connect, GATT discovery,
subscription, whole-record writes and reads, disconnect, shutdown, background
delivery, concurrent request IDs, and at least two independent phone handles
against OTP-06.

**Exclusions:** Production bridge reuse, installers, Veles cryptography, trust
activation, and real OTP data.

**Dependencies:** OTP-01, OTP-02, and OTP-06.

**Acceptance criteria:** On physical Windows and macOS systems, stable Chrome
launches the Rust and Tauri process on demand from a standard non-admin account;
the process creates no window and communicates only through Chrome-owned stdin
and stdout; extension-owned search receives bounded device events; two phones
independently exchange synthetic complete records; an event arrives after the
connector has been backgrounded for ten continuous minutes with another
ordinary tab focused and no audio, WebRTC, or any other timer-throttling
exemption active; disconnect affects only its handle; Chrome port closure,
stdin EOF, and `shutdown` each stop scans, disconnect links, close stdout, and
leave no process; unsupported or denied
states are classified. Any failure blocks OTP-24 or causes this RFC to be
revisited.

**Verification evidence:** Committed harness and physical Windows/macOS matrix
with exact commit, Chrome and OS versions, hardware and adapters, Rust and Tauri
build identity, host manifests, commands, framed stdin/stdout traces, proof that
no window appears, process launch/EOF/port-close/shutdown observations,
repetitions, timings, focused-tab identity, confirmation that no audio, WebRTC,
or other timer-throttling exemption was active, safe logs, per-case
expected/actual results, and an explicit feasibility decision.

**Project fields:** Implementation order 7; Phase Transport; Area Native bridge;
Release scope Stable release; Gate Blocks protected transport; initial Status
Backlog.

### OTP-24: Build the production on-demand Native Messaging BLE bridge

**Outcome:** Chrome can use one bounded, headless, on-demand Rust and Tauri host
for independent BLE byte transport to multiple phones on Windows and macOS.

**Scope:** Implement `hello`, `start_scan`, `stop_scan`, `connect`, `disconnect`,
`send`, and `shutdown`; emit `ready`, `device_found`, `scan_state`, `connected`,
`disconnected`, `sent`, `message`, `shutdown_complete`, and `error`; add strict
versioning, operation-specific request completion, unsolicited lifecycle
ordering, process-local handles, WinRT/CoreBluetooth adapters, bridge-owned GATT
fragmentation/reassembly, bounded ephemeral scan/request/handle/connection/
queue/fragment/reassembly state and in-flight opaque payloads, resource limits,
safe errors, cancellation, cleanup, and deterministic test seams.

**Exclusions:** Installer registration, native UI or persistence, Chrome trust
or cryptography, Veles record parsing, OTP data, and a persistent daemon.

**Dependencies:** OTP-23.

**Acceptance criteria:** Every request and event is bounded and schema-tested;
unknown versions, types, handles, malformed input, and capacity excess fail
closed; concurrent phone bytes never cross handles; one phone failure does not
block another; each accepted request receives exactly one correlated
operation-specific success event or `error`, while unsolicited lifecycle events
carry no request ID; `ready` completes only `hello`; `sent` has only GATT-write
semantics; arbitrary and boundary-sized records round-trip byte-exact through
base64 decode, BLE fragmentation, GATT transfer, reassembly, and base64 encode;
the connector never sees fragments; ephemeral state and in-flight records are
cleared on completion, disconnect, EOF, or exit as applicable; the host persists
no payload or trust state and logs no payload; port closure and shutdown release
all OS resources and terminate; binaries make no network connection and expose
no listener or autostart mechanism.

**Verification evidence:** Rust unit/integration tests, Native Messaging schema
and event-order fixtures, byte-exact base64/fragment/GATT/reassembly round-trip
fixtures across zero, boundary, and multi-fragment records,
WinRT/CoreBluetooth adapter and physical transfer tests, concurrency and
cancellation tests, property/fuzz reports, safe-log and
filesystem/registry/network inspection, process-lifecycle tests, SBOM, and
repeated synthetic physical runs.

**Project fields:** Implementation order 8; Phase Transport; Area Native bridge;
Release scope Stable release; Gate Blocks protected transport; initial Status
Backlog.

### OTP-07: Build the Chrome native-bridge connector and launcher

**Outcome:** The extension reliably opens or focuses one connector tab and the
tab independently manages native-host search and BLE connections to synthetic
phones.

**Scope:** Add the MV3 service worker launcher, connector-context discovery,
responsive connector tab, Native Messaging port and compatibility handshake,
envelope-version bootstrap and bridge API negotiation,
extension-owned search and selection UI, automatic reconnect using
non-authoritative platform hints, request correlation, process-local handle
routing, whole-record `send`/`sent`/`message` handling, per-phone operation
queues, multiple phone state, closure cleanup, and typed
missing/incompatible-host and BLE errors.

**Exclusions:** Installer implementation, cryptographic authentication, OTP
history, automatic copy, and native-owned product UI or storage.

**Dependencies:** OTP-01, OTP-02, and OTP-24.

**Acceptance criteria:** Repeated action clicks focus the existing connector;
only the declared native host and required extension permissions are used;
search results and reconnect status are extension-owned; hints never establish
identity; every phone has isolated requests, handles, state, and queueing;
`sent` completes transport writing but never Android acknowledgement;
compatible extension/host API overlap selects the highest common version;
`incompatible_api` fails closed before BLE operations and presents visible host
or extension install/update guidance; no indefinite backward compatibility is
assumed; closing the tab closes the port and host; reopening reconnects by hint
or offers search; malformed or cross-handle input is bounded and rejected.

**Verification evidence:** TypeScript tests, launcher tests, packaged-extension
browser tests with simulated transport, compatible/incompatible extension-host
version matrix, missing/incompatible-host install/update UX tests, manifest
permission review, and physical search/connect/reconnect smoke tests on Windows
and macOS.

**Project fields:** Implementation order 9; Phase Transport; Area Chrome;
Release scope Stable release; Gate Blocks protected transport; initial Status
Backlog.

### OTP-25: Build signed per-user native-bridge installers

**Outcome:** Windows and macOS users can install, repair, upgrade, and uninstall
the exact-origin production native host without administrator access or stale
registration.

**Scope:** Build per-user Windows and macOS packages with the specified install
roots and Chrome manifest locations; register the production host for only the
exact production extension origin; add atomic upgrade and rollback, uninstall,
Windows code signing, macOS signing/notarization/stapling, signed-to-unsigned
content correspondence, release hashes, checksums, and separate scripted
development registration and removal.

**Exclusions:** Machine-wide installation, auto-update daemons, broad or
wildcard origins, bundling extension trust or OTP state, and Linux packaging.

**Dependencies:** OTP-07 and OTP-24.

**Acceptance criteria:** From standard non-admin accounts on both Windows and
macOS, clean per-user package install, Chrome launch, upgrade, simulated failed
upgrade and rollback, repair, and uninstall all execute successfully without
elevation; the macOS package writes only the documented user locations and
installs no system files or launch agent; the Native Messaging manifest binds
host name `me.nagaev.veles.bridge`, the macOS binary carries bundle identifier
`me.nagaev.veles.bridge`, and another extension origin is rejected; development
registration cannot modify production registration; upgrade preserves a
working prior version until the new signed payload and manifest verify; repair
fixes missing files or registration; uninstall removes the manifest, registry
entry where applicable, binaries, and running host after port closure; Windows
signatures and macOS signatures/notarization/stapling validate on clean systems
with recorded provenance, content correspondence to the reproducible unsigned
inputs, and release hashes. Independent extension-only and host-only updates
succeed when API versions overlap and fail closed with visible install/update
guidance when they do not,
without requiring indefinite backward compatibility.

**Verification evidence:** Installer source and CI logs, package hashes and
SBOMs, signature/notarization/stapling and provenance records,
signed-to-unsigned content correspondence, assertions for host name
`me.nagaev.veles.bridge` and macOS bundle identifier
`me.nagaev.veles.bridge`, exact manifest and registry/filesystem inspection,
standard non-admin clean install/Chrome-launch/repair/upgrade/failed-upgrade/
rollback/uninstall matrices on each OS, macOS user-location and no-elevation
evidence, compatible/incompatible extension-host update matrix, origin negative
tests, development registration tests, and fresh-user walkthroughs on Windows
and macOS.

**Project fields:** Implementation order 10; Phase Transport; Area Native bridge;
Release scope Stable release; Gate Blocks real OTP; initial Status Backlog.

### OTP-08: Establish background liveness and offscreen clipboard support

**Outcome:** A backgrounded connector continues receiving event-driven native
transport without short throttled timers, and synthetic automatic-copy requests
succeed through a clipboard-only offscreen document.

**Scope:** Validate Native Messaging event delivery and protected liveness while
backgrounded; implement service-worker messaging, offscreen-document lifecycle,
copy request validation, result reporting, cleanup, and background-tab and
native-port diagnostics.

**Exclusions:** Real OTP data, moving the native port out of the connector,
persistent offscreen state, and the user-facing automatic-copy setting.

**Dependencies:** OTP-01, OTP-06, and OTP-07.

**Acceptance criteria:** The connector stays backgrounded for at least ten
continuous minutes while another ordinary tab is focused and no audio, WebRTC,
or any other timer-throttling exemption is active; a synthetic native `message`
event and protected push after minute seven are received and copied through the
helper; copy failure is visible and does not disconnect; the helper owns no
native port, session, trust, or history state.

**Verification evidence:** Service-worker/offscreen tests, lifecycle tests,
extension permission/CSP review, and timed physical Windows/macOS background
runs recording the focused ordinary tab and confirming no audio, WebRTC, or
other timer-throttling exemption was active.

**Project fields:** Implementation order 11; Phase Transport; Area Chrome;
Release scope Stable release; Gate Blocks real OTP; initial Status Backlog.

### OTP-09: Implement authenticated reconnect and protected records

**Outcome:** Previously trusted Android and Chrome installations establish
fresh mutually authenticated sessions and exchange only protected synthetic
records.

**Scope:** Implement versioned hellos, canonical transcript, ECDSA signatures,
ephemeral ECDH, HKDF schedule, encrypted key confirmation, directional
AES-GCM records, exact sequence enforcement, protected errors, session teardown,
and reconnect.

**Exclusions:** First-time SPAKE2 enrollment, real OTP payloads, and product UI.

**Dependencies:** OTP-02, OTP-03, OTP-04, OTP-05, OTP-06, and OTP-07.

**Acceptance criteria:** Unknown or revoked peers receive no protected data;
both signatures and confirmations precede authentication; every application
record is encrypted; wrong identity, signature, tag, direction, session,
sequence, type, length, or version fails closed; reconnect never reuses traffic
keys or nonces; no plaintext compatibility path exists.

**Verification evidence:** Cross-language vectors, Android and Chrome state
tests, AEAD and transcript tests, replay/reorder tests, reconnect tests,
malformed-input tests, and synthetic cross-endpoint integration tests.

**Project fields:** Implementation order 12; Phase Security; Area
Cross-platform; Release scope Stable release; Gate Blocks real OTP; initial
Status Backlog.

### OTP-10: Implement one-time SPAKE2 enrollment and trust activation

**Outcome:** A user can pair one phone and one extension installation with a
short code without exposing the code to offline guessing or granting OTP access
after interrupted activation.

**Scope:** Add Android pairing-window state, pairing ID and code generation,
attempt and time limits, extension-owned phone search and selection, Chrome code
entry, SPAKE2 and bidirectional confirmation over opaque native transport,
labeled enrollment-key derivation, encrypted enrollment records,
prepare/commit/ack activation, pending record expiry, authenticated
platform-hint capture, and sensitive-state erasure.

**Exclusions:** Peer-management UI beyond minimal harness controls and real OTP
delivery.

**Dependencies:** OTP-03, OTP-04, OTP-05, and OTP-09.

**Acceptance criteria:** The code is random, six digits, valid for five minutes,
and never sent or logged; one pairing ID allows at most five attempts; wrong,
expired, cancelled, replayed, and interrupted attempts never activate Android
or authorize OTP access; any surviving local state is restricted to activation
recovery; both identities and protocol context are bound; Android permits only
one active pairing window, one SPAKE2 operation in flight, and no more than five
attempts in any rolling five-minute interval; every valid `pB` is charged before
password-dependent work or `cA` and is never refunded; malformed pre-PAKE input
is separately rate and resource limited; both RFC 9382 confirmation messages
pass before enrollment data is released; pending and
`committed_waiting_ack` records authorize only activation recovery and no OTP
data; interruption at every activation message resumes idempotently or expires
without partial access; a native handle or platform hint never substitutes for
the enrolled identity; successful pairing supports later automatic
authenticated reconnect.

**Verification evidence:** Shared enrollment vectors, attempt/expiry and
non-refund tests, malformed-input resource-limit tests, wrong-code and
interruption tests, activation state-machine and recovery tests, state-erasure
tests, and synthetic physical pairing/reconnect tests.

**Project fields:** Implementation order 13; Phase Security; Area
Cross-platform; Release scope Stable release; Gate Blocks real OTP; initial
Status Backlog.

### OTP-11: Implement peer revocation and fail-closed recovery

**Outcome:** Each endpoint can inspect and revoke trusted peers independently,
and key or storage loss returns the affected relationship to an untrusted
state.

**Scope:** Add core peer listing, fingerprints, labels, creation/last-seen
metadata, connected-session lookup, local revocation, protected mutual-removal
request, pending-record and platform-hint cleanup, key-loss detection, and
re-pairing behavior.

**Exclusions:** Final Android and Chrome visual design.

**Dependencies:** OTP-04, OTP-05, OTP-09, and OTP-10.

**Acceptance criteria:** Revoking one peer deletes only its trust and disconnects
matching sessions; an offline counterpart cannot regain access; lost or
corrupt identity/trust state fails closed; re-pairing creates explicit new
trust; labels, native handles, and platform hints cannot change identity;
revocation removes the peer hint and disconnects only matching handles.

**Verification evidence:** Multi-peer repository tests, live-session revocation
tests, mutual/offline removal tests, storage-loss and corruption tests, and
re-pairing integration tests.

**Project fields:** Implementation order 14; Phase Security; Area
Cross-platform; Release scope Stable release; Gate Blocks real OTP; initial
Status Backlog.

### OTP-12: Pass protected synthetic end-to-end validation

**Outcome:** The production-shaped security, transport, background, and
lifecycle path is proven with synthetic data on supported physical platforms
before any real OTP integration.

**Scope:** Using the signed per-user packages, execute missing/incompatible-host
recovery, extension-owned search, SPAKE2 pairing, hint-based authenticated
automatic reconnect, protected pull/push, background native event delivery,
offscreen copy, connector/host closure and restart, revocation, Bluetooth
interruption, and the Android 20-minute foreground-service scenario on stable
Windows and macOS Chrome. The synthetic harness and fixtures must be
client-agnostic: the same SPAKE2 enrollment, reconnect, and protected-record
checks a desktop passes must be reusable by a watch client (and by the virtual
harness validated in OTP-27), without desktop-only assumptions in the fixture
format or validation path.

**Exclusions:** Real notification data and final multi-computer/multi-phone
release validation.

**Dependencies:** OTP-03, OTP-06, OTP-07, OTP-25, OTP-08, OTP-09, OTP-10, and
OTP-11.

**Acceptance criteria:** Every required case passes with no plaintext fallback;
background operation passes OTP-08's ten-continuous-minute/minute-seven scenario
with another ordinary tab focused and no audio, WebRTC, or other
timer-throttling exemption active; after a minute-20 push is scheduled, the
Android task is removed and the phone remains locked for at least 15 minutes,
the foreground indication remains present, a connector pulls after minute 15,
and the scheduled push arrives no earlier than minute 19 and no later than
minute 21 (a one-minute tolerance from the scheduled minute 20); fresh reconnect uses fresh keys;
failures are reproducible and classified; no result relies on an OS bond,
native handle, or platform hint as setup or trust; the synthetic fixtures and
harness are constructed to be client-agnostic — a scripted non-desktop client
fixture passes the same enrollment, reconnect, and protected-record checks as
the desktop connector with no desktop-only assumptions — while running that
fixture end-to-end against the virtual BLE link is validated in OTP-27 and
gates nothing here;
connector closure leaves no host process and restart creates fresh handles and
keys.

**Verification evidence:** A committed matrix containing exact commit SHAs,
phone/desktop models, OS builds, Chrome versions, adapters, steps, repetitions,
timings, focused-tab identity, confirmation that no audio, WebRTC, or other
timer-throttling exemption was active, logs with safe redaction, expected
results, actual results, and pass/fail decisions.

**Project fields:** Implementation order 15; Phase Transport; Area
Cross-platform; Release scope Stable release; Gate Blocks real OTP; initial
Status Backlog.

### OTP-13: Add the memory-only OTP buffer and delivery policies

**Outcome:** Matched Android OTP events enter a bounded, memory-only delivery
domain whose eligibility is controlled immediately before transfer.

**Scope:** Add the desktop delivery coordinator, stable event IDs, five-event
global buffer, ten-minute expiry, remaining-lifetime calculation, sharing
enable/disable lifecycle, and `Unlocked only`, `Display on`, and `Always`
policy evaluation.

**Exclusions:** Bluetooth encoding, Chrome display, durable OTP storage, and
changes to existing local notification behavior.

**Dependencies:** OTP-12.

**Acceptance criteria:** The buffer never exceeds five events or ten minutes;
disable, process death, and reboot clear it; Android is authoritative for
expiry; blocked events can become eligible before expiry; policy checks happen
at delivery time; desktop failures cannot suppress local notifications; OTPs
never enter logs or persistence.

**Verification evidence:** Unit tests with controlled monotonic time, policy
tests for lock/display transitions, process-lifecycle tests, integration tests
with the existing handler path, and persistence/log scans.

**Project fields:** Implementation order 16; Phase Core delivery; Area Android;
Release scope Stable release; Gate None; initial Status Backlog.

### OTP-14: Implement protected pull of recent OTP events

**Outcome:** An authenticated Chrome session can request, decrypt, and merge
currently eligible recent OTP events without extending their lifetime.

**Scope:** Implement the frozen protected pull request, policy-denied response,
empty response, event response, remaining lifetime, acknowledgement,
phone/event deduplication, reconnect pull, and per-phone error handling.

**Exclusions:** Push delivery, final visual polish, and automatic copy setting.

**Dependencies:** OTP-09, OTP-12, and OTP-13.

**Acceptance criteria:** Unknown or unauthenticated peers receive no OTP data;
Android rechecks policy and expiry; Chrome enforces the remaining lifetime and
global five-event limit; repeated pulls deduplicate display history; policy
denial is distinct from empty history; malformed responses fail only the
affected session.

**Verification evidence:** Android and Chrome unit tests, shared record fixtures,
expiry/clock-skew tests, policy tests, deduplication tests, reconnect tests, and
physical protected pull on Windows/macOS.

**Project fields:** Implementation order 17; Phase Core delivery; Area
Cross-platform; Release scope Stable release; Gate None; initial Status
Backlog.

### OTP-15: Implement protected push and independent fan-out

**Outcome:** Android independently attempts protected delivery of each eligible
new OTP to every eligible connected Chrome peer without one peer blocking
another or affecting local behavior.

**Scope:** Connect the coordinator to protected session fan-out; implement the
frozen push and acknowledgement records; add event-reference queues, per-peer
policy and expiry revalidation, just-in-time encryption, failure isolation, and
reconnect semantics.

**Exclusions:** Offline delivery, durable queues, Chrome notifications, and
automatic copy setting.

**Dependencies:** OTP-09, OTP-12, OTP-13, and OTP-14.

**Acceptance criteria:** Android independently queues an event reference for
every peer eligible when the event is offered and records delivery only after
that peer acknowledges it; blocked peers receive no data; slow, disconnected,
failed, or revoked peers cannot block others and are not guaranteed a push;
evicted, expired, or newly blocked queued events are dropped before encryption;
queues do not retain independent OTP copies; failed delivery is not persisted;
reconnect performs a pull for still-retained events; each received event
deduplicates against history; local Android notification behavior is unchanged.

**Verification evidence:** Fan-out and queue tests, policy-transition tests,
acknowledgement/failure tests, slow-peer tests, reconnect tests, and physical
protected push on Windows/macOS.

**Project fields:** Implementation order 18; Phase Core delivery; Area
Cross-platform; Release scope Stable release; Gate None; initial Status
Backlog.

### OTP-16: Add Android Desktop Sharing and Connections UX

**Outcome:** Android users can understand, enable, pair, monitor, configure, and
revoke desktop sharing without exposing sensitive data.

**Scope:** Add the Home card, Connections screen, service enablement and actual
status, pairing action/countdown, delivery-policy selection, trusted and live
client lists, peer details, revocation, foreground-notification navigation,
and actionable capability/permission/service/key-loss errors.

**Exclusions:** A fourth bottom-navigation destination and changes that merge
Chrome trust with sensitive-notification companion association.

**Dependencies:** OTP-06, OTP-10, OTP-11, and OTP-13.

**Acceptance criteria:** Enabled preference and actual availability are not
conflated; all security-relevant actions require clear user intent; pairing
state expires visibly; revocation identifies the affected peer; foreground
notification contains no OTP; screen behavior is accessible and works on
supported phone sizes.

**Verification evidence:** View-model tests, Compose UI tests with stable test
tags, permission/error-state tests, pairing/revocation instrumentation tests,
accessibility checks, and device screenshots for review.

**Project fields:** Implementation order 19; Phase Product UX; Area Android;
Release scope Stable release; Gate None; initial Status Backlog.

### OTP-17: Add Chrome connection, OTP history, badge, and manual-copy UX

**Outcome:** The connector tab presents clear per-phone connection state and a
short-lived, usable OTP history with manual copying.

**Scope:** Add pairing/reconnect controls, remembered-peer display, per-phone
search and status, eligible event cards, source phone, code, merchant, amount,
age, manual copy, expiry, global history cap, badge updates,
missing/incompatible-host install and repair actions, stale-hint recovery,
empty/policy/error states, and responsive/accessibility behavior.

**Exclusions:** Automatic-copy preference and desktop notifications.

**Dependencies:** OTP-07, OTP-10, OTP-11, and OTP-14.

**Acceptance criteria:** History appears immediately from valid session
storage; expired entries and badge counts clear on time; manual copy requires
explicit action and reports failure; multiple phones remain distinguishable;
errors are specific without exposing secrets; no OTP enters local or sync
storage; missing-host and repair UI preserves peer trust and can retry after
installation without reloading unrelated state.

**Verification evidence:** State and component tests, expiry/deduplication/badge
tests, copy tests, accessibility checks, packaged-extension browser tests, and
Windows/macOS UI validation.

**Project fields:** Implementation order 20; Phase Product UX; Area Chrome;
Release scope Stable release; Gate None; initial Status Backlog.

### OTP-18: Add opt-in automatic copy and visible failure handling

**Outcome:** Users can explicitly enable predictable automatic copying for
pulls and pushes, including while the connector tab is backgrounded.

**Scope:** Add the global disabled-by-default setting, settings explanation,
pull/push trigger rules, service-worker/offscreen request flow, success/failure
feedback, duplicate-event semantics, and setting persistence without OTP
storage.

**Exclusions:** Per-bank rules, per-phone rules, clipboard history management,
and desktop notifications.

**Dependencies:** OTP-08, OTP-15, and OTP-17.

**Acceptance criteria:** Enabling requires explicit action; every successful
pull copies its newest event, including a repeated pull; every new push copies
its event; the offscreen helper receives only the immediate request; failure is
visible and does not disconnect; disabling stops all automatic copy; no OTP is
persisted durably.

**Verification evidence:** Settings tests, pull/push copy tests, repeated-pull
tests, background helper tests, failure tests, storage inspection, and timed
physical Windows/macOS runs.

**Project fields:** Implementation order 21; Phase Product UX; Area Chrome;
Release scope Stable release; Gate None; initial Status Backlog.

### OTP-19: Complete multi-phone and multi-computer isolation

**Outcome:** Required multi-peer topologies work concurrently with independent
trust, security state, queues, UI, and failures.

**Scope:** Complete one-phone/two-computer and one-connector/one-bridge/multiple-
phone behavior across pairing, reconnect, pull, push, copy, revocation,
interruption, request correlation, process-local handle routing, and capacity
handling. Document best-effort behavior for multiple profiles on one adapter.

**Exclusions:** Guaranteed multi-profile support and cloud coordination.

**Dependencies:** OTP-14, OTP-15, OTP-16, OTP-17, and OTP-18.

**Acceptance criteria:** One phone independently serves Windows and macOS; one
connector independently serves multiple phones; event source is unambiguous;
native handles, session keys, sequence spaces, queues, and revocation never
cross peers; a slow, failed, or revoked peer cannot affect another; capacity
rejection is explicit and safe.

**Verification evidence:** Multi-peer unit/integration tests and a repeated
physical matrix covering both topologies, source-specific pulls/pushes,
background copy, interruption, and revocation with exact environments.

**Project fields:** Implementation order 22; Phase Hardening; Area
Cross-platform; Release scope Stable release; Gate Blocks release; initial
Status Backlog.

### OTP-20: Harden lifecycle, limits, malformed traffic, and recovery

**Outcome:** The complete feature fails closed and recovers predictably across
hostile input, platform lifecycle changes, resource pressure, and endpoint
state loss.

**Scope:** Complete bounds enforcement, parser/property/fuzz testing,
replay/reorder/counter-boundary handling, queue pressure, Bluetooth toggles,
permission loss, task/process/browser/host restart, native-port failure, host
upgrade/removal, stale hints and handles, service restoration, key loss, storage
corruption, connector closure, safe diagnostics, and regression matrix.

**Exclusions:** New product capabilities and compatibility downgrades.

**Dependencies:** OTP-19.

**Acceptance criteria:** Every specified bound is enforced without secret
leakage; malformed or cryptographically invalid input mutates no protected
state; terminal protocol errors erase session keys; lifecycle events never
reuse nonce/sequence state; recovery never restores expired OTPs; safe logs
contain no prohibited data; local Android notifications remain reliable; the
linked complete evidence is reviewed and the release owner records explicit
Gate C acceptance before the issue closes.

**Verification evidence:** Property/fuzz reports, boundary and lifecycle tests,
safe-log scans, backup/storage-loss tests, Android and Chrome restart tests,
Bluetooth/permission fault injection, and complete physical regression results.

**Project fields:** Implementation order 23; Phase Hardening; Area
Cross-platform; Release scope Stable release; Gate Blocks release; initial
Status Backlog.

### OTP-21: Pass release-candidate security, packaging, and platform review

**Outcome:** The exact release candidate is independently reviewed and minimally
privileged, its unsigned native release inputs are byte reproducible, and its
signed outputs are provenance-verified for Chrome Web Store and Android release
processes.

**Scope:** Commission external security review covering the Native Messaging
envelope/parser/state machine, request correlation and handle routing,
bounds/queues/framing, WinRT/CoreBluetooth adapters, Tauri headless lifecycle,
exact-origin host registration, and installer install/repair/upgrade/rollback/
uninstall behavior in addition to the protected protocol and cryptography;
remediate and re-review findings; benchmark SPAKE2;
verify ABI/CSP/permissions/local WASM and native-host privilege/network
boundaries; reproduce unsigned bridge binaries, bundle/package payloads,
manifests, and installer inputs in independent clean builds; validate Windows
signatures and macOS signatures/notarization/stapling, provenance,
signed-to-unsigned content correspondence, release hashes, licenses, and SBOM;
run stable Windows/macOS and API-compatibility matrices; prepare Web Store
disclosures and release evidence.

**Exclusions:** Waiving findings, unreviewed cryptographic profile changes,
machine-wide native installation, and public release before OTP-22.

**Dependencies:** OTP-20.

**Acceptance criteria:** External review accepts all listed native and protected
protocol surfaces in the exact relevant artifacts; all findings are resolved
and affected artifacts are re-reviewed; SPAKE2 completes within one second
on each endpoint in the approved representative minimum/median matrix;
independent clean builds produce byte-identical unsigned native outputs and
installer inputs; signed outputs need not be byte-identical but have valid
platform trust, provenance, content correspondence to those unsigned inputs,
and recorded release hashes; no remote code or excess permission exists;
install, repair, upgrade, rollback, and uninstall matrices pass; compatible
extension/host API versions negotiate and incompatible versions fail closed
with visible install/update guidance; supported platform matrices pass; Web
Store disclosures accurately state local data, permissions, retention, native
host use, and no-cloud/no-telemetry behavior.

**Verification evidence:** Review report covering every listed native surface
and remediation record, benchmark report, independent unsigned build logs and
byte-for-byte hash comparisons, signature/notarization/stapling and provenance
records, signed-to-unsigned content correspondence, release hashes,
ABI/CSP/permission inspection, SBOM/licenses, compatible/incompatible
extension-host update matrix, physical platform matrix, and draft Web Store
submission materials.

**Project fields:** Implementation order 24; Phase Release; Area
Cross-platform; Release scope Stable release; Gate Blocks release; initial
Status Backlog.

### OTP-22: Publish setup, security, privacy, support, and troubleshooting documentation

**Outcome:** Users and maintainers can install, use, assess, recover, and support
the feature without misunderstanding its local data boundary or security model.

**Scope:** Update README and GitHub Pages with Android/Chrome installation,
Desktop Sharing, per-user native-host install/repair/upgrade/uninstall,
connector and host lifecycle, extension-owned search, SPAKE2 pairing,
authenticated automatic reconnect and hint fallback, delivery policies,
retention, copy behavior, revocation, key-loss recovery, foreground-service
behavior, supported platforms, Windows stale-bond troubleshooting,
security architecture, and privacy language.

**Exclusions:** Claims of cloud capability, telemetry, OS-pairing or platform-
hint trust, hidden background Web Bluetooth, Linux implementation or support
guidance, claims of a persistent native process, daemon, or agent, and desktop
notifications.

**Dependencies:** OTP-21.

**Acceptance criteria:** Documentation matches the release candidate and Web
Store disclosures; it clearly states that the connector tab must stay open,
the native host runs only on demand, OTP history is short-lived, plain GATT
carries encrypted Veles records opaque to the bridge, OS pairing and platform
hints are not trust, companion association is independent, and OTP data never
transits an internet service; support steps do not request sensitive logs.

**Verification evidence:** Documentation review against the release candidate,
link and command checks, privacy/security review, fresh-user setup walkthroughs
on Windows and macOS, and final release checklist approval.

**Project fields:** Implementation order 25; Phase Release; Area Documentation;
Release scope Stable release; Gate Blocks release; initial Status Backlog.

### Watch client (T-WATCH-S3)

The second client class is a LILYGO T-WATCH-S3 family (ESP32-S3) smartwatch
that pairs over the same protected plain-GATT protocol and receives OTP pushes
on the wrist. The protocol fits without divergence:

- Android is the BLE peripheral/GATT server; clients are centrals speaking
  opaque, encrypted Veles records. Nothing in the record model, session crypto,
  enrollment, delivery policies, or retention caps assumes a desktop, a tab, or
  the Native Messaging bearer. The desktop-specific layer is strictly the
  client-side bearer (Native Messaging + the Rust/Tauri bridge); a watch speaks
  GATT directly and needs none of it.
- SPAKE2 uses no memory-hard preprocessing, so the watch runs the very profile
  the Chrome connector runs — the same pinned Rust core (OTP-03 gains an Xtensa
  firmware target), the same six-digit code ceremony, the same limits. There is
  no embedded-memory carve-out and no unreviewed downgrade. (This was the
  blocker under OPAQUE and is the principal reason SPAKE2's selection unlocks
  watch support.)
- The resource caps (8 simultaneous clients, 2 incomplete messages / 8 KiB
  reassembly per peer, queue bounds) and the multi-peer isolation model apply
  to watch peers unchanged. Delivery policies remain phone-side and apply to
  watch pushes. OTP-02's fixture set is host-runnable Rust so the watch crate
  runs the same vectors; its schema freeze adds the enrollment `device_type`
  and directs GATT layout and fragmentation bounds so a bare GATT client at a
  negotiated MTU of at least 185 works, with reassembly staying within the
  8 KiB/peer cap. Android treats all watches as ordinary peers — no new pairing
  windows or limits.
- Watch firmware beyond this protocol surface is deferred (see Rejected and
  deferred alternatives); the design sketch is in the client-design appendix.

## Ordered execution tasks

### OTP-26: Validate esp-rs toolchain, NimBLE BLE central, and SPAKE2 core on Xtensa

**Outcome:** Evidence that the pinned Rust SPAKE2 core (OTP-03) and a
production-grade Rust BLE GATT central both build and run for ESP32-S3 class
silicon, with rough timing data, before any firmware is committed to.

**Scope:** In a disposable harness, build the OTP-03 SPAKE2 core against
Xtensa targets via the esp-rs toolchain (ESP-IDF `linux` host target for fast
iteration, then `qemu-system-xtensa` ESP32-S3 machine for target-arch sanity
and rough timing); validate a host-side functional BLE round-trip with a
supported host stack (a scripted Bumble central over a virtual HCI controller,
or an explicitly integrated native NimBLE Linux port) performing
scan/connect/GATT client against a synthetic peer — ESP-IDF's `bt` component
does not build for the `linux` target, so `esp32-nimble` cannot execute on the
host; separately build the selected `esp32-nimble` central crate/configuration
for the ESP32-S3 target and boot it under `qemu-system-xtensa` as a target
sanity check (compile, link, boot; radio behavior requires real hardware and
is deferred to the follow-up firmware RFC); measure SPAKE2 wall time and
memory ceiling on the emulated CPU; document crate/toolchain pins, build
friction, and any central-role API gaps. Also survey the board-support gap
(ST7789 display, FT6336 touch, AXP2101 PMU) to size OTP-28-class BSP work.

**Exclusions:** Veles UI, OTP data, trust storage, real hardware, and anything
kept. Spike output is throwaway.

**Dependencies:** OTP-01, OTP-02, OTP-03.

**Acceptance criteria:** SPAKE2 client login completes against the RFC vectors
on the Xtensa build; the selected `esp32-nimble` central crate/configuration builds
and links for the ESP32-S3 target and boots under `qemu-system-xtensa`;
a supported host BLE central (scripted Bumble over virtual HCI, or an
explicitly integrated native NimBLE Linux port) performs scan → connect → GATT
discover → notify round-trip against a synthetic GATT server;
SPAKE2 timing and
peak-heap figures recorded with the emulation-speed caveat (±50% until
hardware); any blocker or crate gap is written up with an explicit go/no-go
recommendation for Approach-A firmware. Emulation limitations (timing accuracy,
no real radio) are stated in the report.

**Verification evidence:** Committed spike harness, build logs, vector results,
central round-trip traces, measured timings with hardware specs of the host VM,
and the written feasibility decision.

**Project fields:** Implementation order 26; Phase Foundations; Area
Cross-platform; Release scope Deferred; Gate None; initial Status Backlog.

### OTP-27: Validate virtual-BLE end-to-end harness (Android emulator ↔ simulated watch)

**Outcome:** Proof that the protected synthetic transport can be exercised
end-to-end without physical BLE hardware, so watch-side development and CI do
not wait for a device.

**Scope:** In a disposable harness, run the Veles Android sharing service in
the Android emulator backed by a virtual Bluetooth controller (rootcanal or
Bumble over virtual HCI), and a simulated watch client (a scripted Bumble
central, or an explicitly integrated native NimBLE Linux port from OTP-26)
as the central; carry OTP-02 frozen-format synthetic records end to end
(scan/connect, SPAKE2 enrollment with a fixture code, hello/reconnect,
push + ack). Measure setup reliability and document which layers are real
(procedure PDUs, crypto) vs emulated (radio, timing).

**Exclusions:** Real radios, real phones/watches, OTP data, and any production
code. Spike output is throwaway.

**Dependencies:** OTP-06, OTP-09, OTP-10, OTP-26.

**Acceptance criteria:** Synthetic enrollment + one push + ack traverse the
emulated link with all protocol checks passing; setup steps are documented
well enough for a second machine to reproduce; the report states which
Gate-B/C acceptance criteria can adopt this harness and which still demand
physical devices.

**Verification evidence:** Committed harness, emulator/controller
configuration repo files, end-to-end traces, reproduction instructions, and the
written harness-adoption recommendation.

**Project fields:** Implementation order 27; Phase Transport; Area
Cross-platform; Release scope Deferred; Gate None; initial Status Backlog.

Canonical order: OTP-01, OTP-02, OTP-03, OTP-04, OTP-05, OTP-06,
OTP-23, OTP-24, OTP-07, OTP-25, OTP-08, OTP-09, OTP-10, OTP-11, OTP-12,
OTP-13, OTP-14, OTP-15, OTP-16, OTP-17, OTP-18, OTP-19, OTP-20, OTP-21,
OTP-22, OTP-26, OTP-27. OTP-26 may start as soon as OTP-03 lands; OTP-26 and
OTP-27 are intentionally sequenced last as deferrable parallel work and gate
nothing above.

## Appendix A: watch-client design sketch (non-normative)

The following watch decisions are agreed at design level but are **intentionally
not normative here**, to keep this RFC scoped to the protocol while spikes run.
They become binding in the follow-up amendment or RFC after OTP-26/OTP-27:

- Firmware architecture: monorepo `watch/` layout with `watch/core`
  (platform-agnostic Rust protocol crate reusing the OTP-03 core),
  `watch/lilygo/hal` (BSP traits/drivers: ST7789/FT6336/AXP2101),
  `watch/lilygo/twatch-s3` (feature flags `s3`/`s3plus`), and future
  `watch/lilygo/twatch-ultra`.
- PIN UX details: set-at-pairing on the watch's dial, change-PIN, grace
  window.
- Push UX: wake on push, notification card, tap to reveal behind PIN, OTP list
  (≤5), TTL countdown.
- Watch execution tasks (firmware skeleton, UI pipelines, hardening, Gate B/C
  participation) and their dependencies.
- Real-battery-life numbers stay a hardware-validation item, not an RFC
  commitment.

The firmware is expected to be pure Rust on ESP-IDF std
(`esp-idf-svc` + `esp32-nimble` host + RustCrypto `p256`/`aes-gcm`/`hkdf`/
`sha2` in software, fast enough at <1 KiB records), UI via Slint or
embedded-graphics, board support via a small `Board` HAL over AXP2101/FT6336/
ST7789. A C++/LVGL hybrid with the SPAKE2 core linked as a Rust staticlib is
the recorded fallback if BSP bring-up dominates; a bare-`no_std`/`trouble`
stack was considered and rejected as too immature for a security client today.
These sketches inform OTP-26's survey and are superseded by whatever the
follow-up amendment freezes.
