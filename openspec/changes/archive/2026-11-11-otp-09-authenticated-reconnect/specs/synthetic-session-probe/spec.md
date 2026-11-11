## ADDED Requirements

### Requirement: Debug-only trust injection
Debug Android builds and development-channel extension builds MUST provide a hook that
exports the local installation id and identity public key. The same hook MUST install a
supplied peer record directly as `active`, bypassing the activation proofs. The installed
records MUST be ordinary trust records, and the unchanged handshake MUST authenticate
against them.

#### Scenario: Cross-install trust
- **WHEN** each side's exported identity is injected into the other side
- **THEN** a reconnect between them authenticates, and fixes class `full`

### Requirement: Trust injection is unreachable from web content
The Chrome injection hook MUST be reachable only from trusted extension contexts. It MUST
reject calls from web pages, from content scripts, and from any sender outside the
extension.

#### Scenario: Web content cannot reach the hook
- **WHEN** a web page or content script tries to call the injection hook
- **THEN** the call is rejected, and no trust record changes

### Requirement: Synthetic event source
In debug builds, the phone MUST register a `full`-session application handler that answers
`pull_request` with `pull_events` containing exactly one §8.1 event. The event MUST carry a
fresh random marker as the code, merchant `Veles synthetic`, amount 100 at scale 2, currency
`XTS`, and `remaining_lifetime` ≤ 600. It MUST NOT read real notifications or `OtpMessage`
data. It MUST NOT serve as OTP-12's Gate B fixture or OTP-13's buffer.

#### Scenario: Pull round trip
- **WHEN** the connector sends `pull_request` in a `full` session with a debug phone
- **THEN** the phone returns exactly one synthetic event, and Chrome parses it and sends a
  matching `event_ack`

#### Scenario: No real data
- **WHEN** a real OTP notification is present on the phone
- **THEN** the probe's response still contains only its synthetic event

### Requirement: Phone-initiated synthetic push
A debug adb command MUST send exactly one synthetic event as `push_event` over the currently
open `full` session. If no such session is open, the command MUST do nothing.

#### Scenario: Push round trip
- **WHEN** the adb push command runs while a session is open
- **THEN** Chrome receives, parses and acknowledges one synthetic `push_event`

#### Scenario: No session open
- **WHEN** the adb push command runs with no open session
- **THEN** nothing is sent

### Requirement: Strict client parsing and acknowledgement
The Chrome development build MUST decrypt each synthetic event and parse it strictly per
§8.1, then send `event_ack` carrying the phone installation id and the event id. If an event
violates a §8.1 bound, Chrome MUST reject the record and end the session.

#### Scenario: Malformed synthetic event
- **WHEN** a probe event exceeds a §8.1 bound
- **THEN** Chrome rejects it, ends the session, and shows nothing in the readout

### Requirement: Visible debug readout
The development-channel connector MUST show the decoded synthetic event and the session id
prefix in a debug-only readout. The debug phone MUST log the same marker and event id to
logcat. This is the only exception to RFC safe observability, and it MUST be limited to debug
builds and synthetic data.

#### Scenario: Matching marker
- **WHEN** a synthetic event makes the round trip
- **THEN** the marker in the connector readout equals the marker in the phone's logcat line

#### Scenario: New session after reconnect
- **WHEN** the link drops and the probe runs again after reconnecting
- **THEN** the readout shows a different session id prefix

### Requirement: Release artifacts exclude test hooks
The release APK (after R8) and the production extension bundle MUST NOT contain the
trust-injection entry points, the probe handler, the debug readout, or the probe's
identifying strings. Automated guard tests on the built artifacts MUST enforce this.

#### Scenario: Release APK guard
- **WHEN** the release guard test inspects the minified APK
- **THEN** none of the hook classes, receivers or probe strings are present

#### Scenario: Production bundle guard
- **WHEN** `test:bundle` inspects the production extension bundle
- **THEN** none of the injection, probe or readout code or strings are present

### Requirement: Physical round-trip evidence
The change MUST record a physical run between a real phone and Chrome on both Windows and
macOS, performed through the homepc tools and adb. The results MUST go in a dated
`docs/otp-09-*.md` note that follows safe redaction. The note MUST record the commit, the
devices, the OS builds, the Chrome version, and pass or fail for each step.

#### Scenario: Evidence steps
- **WHEN** the evidence note is reviewed
- **THEN** for both Windows and macOS it shows, in order:
  1. trust injection
  2. authentication
  3. pull and ack
  4. adb push and ack
  5. an idle heartbeat
  6. a reconnect with a new session id
  7. revocation with the generic failure
  
  Each step has its observed result.

#### Scenario: Revocation in the physical run
- **WHEN** the injected trust record is removed on the phone and Chrome reconnects
- **THEN** Chrome shows the generic authentication failure and receives no protected data
