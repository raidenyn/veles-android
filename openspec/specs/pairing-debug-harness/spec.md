# pairing-debug-harness Specification

## Purpose
Debug-only Android and Chrome controls that open a pairing window, show the code on screen, cancel an attempt, and clear a stranded trust record, while keeping that whole surface out of release artifacts.

## Requirements

### Requirement: Debug-only Android pairing controls
The Android pairing controls (Start, Cancel, on-screen code, "Clear incomplete record") MUST be
available only in debug builds, in a non-launcher-visible screen. They MUST be absent from
release APKs.

#### Scenario: Start and cancel
- **WHEN** Start is pressed in a debug build
- **THEN** a window opens and the code is shown on screen, and Cancel closes it

#### Scenario: Release APK
- **WHEN** the release APK is scanned after R8
- **THEN** the debug pairing package, actions and screen strings are absent

### Requirement: Debug-only Chrome pairing panel
The Chrome code-entry panel with Cancel and Clear MUST be behind the build-time
`__VELES_CHANNEL__` constant and a dynamic import, reusing the extension-owned phone search
and selection. It MUST be absent from the production bundle.

#### Scenario: Code entry
- **WHEN** a code and a phone are selected in a debug build
- **THEN** pairing starts and the panel shows state and the fingerprint

#### Scenario: Production bundle
- **WHEN** `bundle.test.ts` inspects the production build
- **THEN** it contains no debug panel text

### Requirement: Code readout never logged
The code MUST be exposed only as on-screen text and an in-process `StateFlow` for
instrumentation. A logcat line, broadcast, notification, Chrome `DiagnosticRing` entry or
view-model snapshot MUST NOT contain it. Scripted broadcasts MUST NOT echo it.

#### Scenario: Logger guard
- **WHEN** the guard test scans the source
- **THEN** it fails if `PairingCode` is passed to a logger

#### Scenario: Broadcast start
- **WHEN** a debug broadcast starts a window
- **THEN** nothing in its result or logs includes the code

### Requirement: Clear incomplete record
A clear control on each side MUST remove a stranded or injected record for the chosen peer so
re-pairing is possible without a separate revocation step. Both clear controls MUST go through the
revocation coordinator, so any open session of that peer is terminated instead of the clear being
refused. The Android clear MAY keep using the debug `REVOKE_PEER` broadcast as its entry point.

#### Scenario: Clear then re-pair
- **WHEN** a stranded `pending` record is cleared on both sides
- **THEN** a new pairing succeeds

#### Scenario: Clear with an open session
- **WHEN** a record is cleared while an authenticated session of that peer is open
- **THEN** the record is deleted and that session is terminated

### Requirement: Release exclusion guards
`releaseHookGuard` MUST scan the R8 output for the debug pairing surface, including the peer
management controls and their strings, through its `releaseHookForbidden` list, and
`bundle.test.ts` MUST check the production bundle for the same strings. The pairing engines and
the revocation coordinator MAY link into release builds, but release builds MUST NOT be able to
open a window or expose the debug controls.

#### Scenario: Release pairing_request
- **WHEN** a release build receives `pairing_request`
- **THEN** it sends the generic rejection

#### Scenario: Peer controls absent from release
- **WHEN** the release APK and the production bundle are scanned
- **THEN** none of the peer-management debug strings appear

### Requirement: Physical runs
A physical phone-to-Chrome run on Windows and on macOS MUST start by clearing injected trust
on both sides, then cover pairing, a first user-selected and a second automatic reconnect, and an interrupted
activation with the connector tab kept open. The record in `docs/` MUST include only observed
values.

#### Scenario: Run record
- **WHEN** the runs finish
- **THEN** `docs/` records both platforms, the fingerprints seen, and the first (selected) and
  second (automatic) reconnect

### Requirement: Debug-only peer management controls
The Android debug pairing screen and the Chrome debug pairing panel MUST list peers and offer
Revoke and Mutual remove actions for the chosen peer, and an endpoint-wide Reset action, all
backed by the revocation coordinator. The controls MUST be
available only in debug builds and the development bundle, and MUST NOT introduce final UI.

#### Scenario: Revoke from the control
- **WHEN** Revoke is used on a listed peer
- **THEN** the peer's record is deleted, its sessions close, and the list no longer shows it

#### Scenario: Mutual remove with no connection
- **WHEN** Mutual remove is used on a peer with no open full session
- **THEN** the control reports that only local removal is possible and deletes nothing

### Requirement: Debug reset is destructive and confirmed
The debug Reset destroys every local trust record and the identity, so it MUST be labeled
destructive and MUST require an explicit confirmation (an intentional refinement of the design's
explicit-action rule).

#### Scenario: Reset confirmation
- **WHEN** Reset is activated without confirmation
- **THEN** nothing is deleted
