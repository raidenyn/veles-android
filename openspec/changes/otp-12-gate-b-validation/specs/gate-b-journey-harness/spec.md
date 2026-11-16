## ADDED Requirements

### Requirement: Client-agnostic journey file
`protocol/conformance/gate-b-journeys.json` MUST define two independent journeys, `enrollment` (run on the existing pairing loopback mode) and
`protected-records` (run on the test-only loopback main), as ordered lists of protocol-level actions (`enroll`, `reconnect`, `seed`, `pull`,
`schedule_push`, `push`, `ack`, `advance_clock`, `revoke`, `reconnect_expect_rejected`) with expectations (session
class, event count, remaining-lifetime ranges, `key_fresh` meaning the session key material differs from the previous session's, rejection). All keys, action names, parameter names and string values MUST NOT contain desktop vocabulary: after lowercasing and splitting on non-alphanumerics and camelCase boundaries, no token may equal tab, port, native, host, handle, extension, offscreen or badge (so `transport` and `support` are allowed). A platform hint MUST appear only as the top-level property `trust_independent_of_platform_hint`; any other token `hint` fails. A schema test in `protocol/checks/`, added to the named list in `check.mjs`, MUST enforce this and run in CI.

#### Scenario: Desktop vocabulary
- **WHEN** a journey action or parameter contains a forbidden word
- **THEN** the schema test fails

#### Scenario: Token matching
- **WHEN** a parameter is named `transport_mtu`
- **THEN** the vocabulary test passes, while a parameter named `tab_id` fails it

#### Scenario: Unknown action
- **WHEN** a journey uses an action outside the list
- **THEN** the schema test fails

#### Scenario: Existing conformance files untouched
- **WHEN** the existing Kotlin and TypeScript conformance runners run
- **THEN** they read only their own files and pass unchanged

### Requirement: Scripted non-desktop client
A scripted client under `web-extension/test/gate-b/` MUST run both journeys against the real
Kotlin phone engine in the child-JVM loopback, using the production TypeScript client engine and the
pairing core and activation engine directly, with its own in-memory implementation of the trust
repository interface. It MUST NOT import `src/trust/repository.ts`, `src/pairing/link.ts` or
`src/trust/storage.ts`. An import-guard test MUST compute the transitive import closure of the
client's files and fail if it reaches `src/connector/**`, `src/background/**`, `src/offscreen*`,
`src/content*`, `src/debug/**`, `src/pairing/link.ts`, `src/trust/storage.ts` or native-bridge code,
or if those files mention `chrome.`, `deviceHandle` or `connectionHandle`. If the closure cannot be
made clean without a production edit, the guard MAY fall back to a direct-import allow-list
(including `rust-wasm/pkg`) plus the identifier scan, and the divergence MUST be recorded.

#### Scenario: Enrollment journey passes
- **WHEN** the scripted client runs the `enrollment` journey
- **THEN** enrollment, activation and a reconnect with fresh keys all meet their expectations

#### Scenario: Protected-records journey passes
- **WHEN** the scripted client runs the `protected-records` journey
- **THEN** pull, push, ack, expiry after a clock advance and the rejection after revocation meet their expectations

#### Scenario: Forbidden import
- **WHEN** a client file imports `src/connector/port.ts` directly or transitively
- **THEN** the import-guard test fails

#### Scenario: CI does not skip
- **WHEN** CI runs `sessionLoopbackCheck`, which sets `VELES_LOOPBACK_REQUIRED=1`
- **THEN** the scripted-client tests run and a missing loopback classpath is a failure; a bare local run without that variable skips, as the existing loopback test does

#### Scenario: Divergence recorded
- **WHEN** the import guard uses its direct-import fallback
- **THEN** the divergence is recorded in the guard test's header comment and in the apply report

### Requirement: Test-only loopback main
The protected-records journey MUST run against a new child-JVM main in `android/app/src/testDebug`
that wires the real Gate B fixture on a manual clock, with `seed <lifetimes>`, `schedule <delay>`, `advance <seconds>` and `revoke` commands (the `pull` and `ack` actions use the session itself, and `push` means expecting a `push_event` after `schedule_push` plus `advance_clock`) and a
seed-derived in-memory `ACTIVE` record standing in for an enrolled peer. It MUST NOT be compiled
into release unit tests or any shipped artifact. The shared loopback main's `private` helpers MAY be
loosened to `internal` for it. No journey test MAY sleep or read the wall clock.

#### Scenario: Expiry without sleeping
- **WHEN** the journey advances the manual clock past a record's lifetime
- **THEN** the next pull omits that record and the test used no real delay

#### Scenario: Revocation
- **WHEN** the journey revokes the peer and reconnects
- **THEN** the reconnect is rejected with the generic failure and no protected data is delivered

#### Scenario: Release unit tests
- **WHEN** the release unit-test source set is compiled
- **THEN** the loopback main is not part of it
