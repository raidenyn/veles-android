## ADDED Requirements

### Requirement: Debug-only five-record fixture
The debug Android build MUST provide a Gate B history fixture that keeps at most five synthetic
records in memory. A seed command MUST create exactly five records with fresh random markers,
merchant `Veles synthetic`, amount 100 at scale 2 and currency `XTS`, each with a fixed event id
and a lifetime taken from a command argument list of five integers in 1..600 (default five values
of 600). A list that is not exactly five values in range MUST be refused and MUST change nothing.
Each record's receipt epoch MUST be the seed's wall-clock seconds. A seed MUST replace only the history, MUST arm the fixture and MUST NOT affect a scheduled push; a separate disarm command MUST return answering to the probe.
The fixture MUST NOT read notifications, `OtpMessage` data or the clipboard, MUST NOT persist
across a process restart and MUST NOT serve as OTP-13's buffer.

#### Scenario: Seed five records
- **WHEN** the seed command runs with the default lifetimes while the sharing service runs
- **THEN** the fixture holds five records, each with a distinct event id, and is armed

#### Scenario: Bad lifetime list
- **WHEN** the seed command runs with four values, or a value of 0 or 601
- **THEN** the command is refused, the history and armed state are unchanged and the refusal is logged

#### Scenario: Seed without a running service
- **WHEN** the seed command runs while no sharing service is running
- **THEN** it is refused and logged and nothing is armed

#### Scenario: Process restart
- **WHEN** the app process is killed and restarted
- **THEN** the fixture is unarmed and holds no records

### Requirement: One responder per pull
While armed, the fixture MUST answer `pull_request` with one `pull_events` record, and the
synthetic probe MUST NOT answer. The choice between probe and fixture MUST be made once per pull,
so a mode change during a pull never leaves a pull unanswered or answered twice. Disarm, a service
stop or a process restart MUST return answering to the probe. The router MUST be absent when the installation identity is unavailable, as the probe is today, and MUST log which component answered each pull. `event_ack` MUST go to the component that owns the id: an ack for a fixture-pushed event is logged by the fixture and an unknown id is the OTP2-REC-032a no-op.

#### Scenario: Two sessions, one reply each
- **WHEN** two authenticated sessions each send `pull_request` while armed
- **THEN** each receives exactly one `pull_events` record from the fixture

#### Scenario: Mode change during a pull
- **WHEN** a test seam flips the mode after the engine's `handles` check and before the router's `handle` call
- **THEN** the pull is answered exactly once by the component the router's single mode read selected, and the session stays open

#### Scenario: Ack routing
- **WHEN** a client acknowledges a fixture-pushed event, a history record and an unknown id
- **THEN** the first two are logged by the fixture and the third changes nothing and is not an error

#### Scenario: Disarm
- **WHEN** the disarm command runs
- **THEN** the next pull is answered by the probe and any scheduled push is cancelled

### Requirement: Per-record expiry
Each record's expiry MUST be the seed's elapsed-realtime plus the record's lifetime. A pull MUST
return, in seed order, exactly the records with at least one whole second remaining, each re-encoded
with its fixed event id and `remaining_lifetime` equal to the floor of the remaining seconds
(1..600). A record with less than one second remaining MUST be omitted and never sent with
`remaining_lifetime` 0. An empty result MUST be `event_count = 0`. `event_ack` MUST be logged and
MUST NOT remove a record.

#### Scenario: Retained and expired together
- **WHEN** the clock advances so that the records seeded with the shortest lifetimes have expired
- **THEN** a pull returns only the unexpired records, each with its original event id and a smaller `remaining_lifetime`, and omits the expired ones

#### Scenario: All expired
- **WHEN** every record has expired
- **THEN** a pull returns `event_count = 0`

#### Scenario: Ack does not delete
- **WHEN** a client acknowledges a record and pulls again before expiry
- **THEN** the record is returned again

#### Scenario: Boundary
- **WHEN** a record has exactly 0.5 seconds remaining
- **THEN** it is omitted

### Requirement: Scheduled synthetic push
A schedule command MUST take a delay in whole seconds from 1 to 7200 (anything else is refused and logged), MUST require an armed fixture, and MUST schedule exactly one push. The deadline MUST be computed on the elapsed-realtime clock and the push MUST NOT fire before it, and MUST fire as soon as the clock reaches it even after deep sleep, regardless of whether the timer is a stepped wait with a wake lock or an exact alarm. A wake resource held for the schedule MUST be released when the push fires, is skipped or is cancelled, and on detach, with a timed backstop of the delay plus 60 seconds. At the deadline the fixture MUST send one fresh synthetic `push_event` per currently open `full` session through the same path as the adb push, or record `skipped` when none is open. The pushed events MUST NOT join the history.

#### Scenario: Push fires to an open session
- **WHEN** a schedule is set and a `full` session is open at the deadline
- **THEN** that session receives one synthetic `push_event` and a `fired` outcome is logged

#### Scenario: No session at the deadline
- **WHEN** no `full` session is open at the deadline
- **THEN** nothing is sent and `skipped` is logged

#### Scenario: Clock steps without sleeping
- **WHEN** a test advances the injected clock past the deadline
- **THEN** the push fires without any real waiting

#### Scenario: Not before the deadline
- **WHEN** the injected clock stands one second before the deadline
- **THEN** no push is sent

#### Scenario: Delay out of range
- **WHEN** the schedule command runs with 0, a negative value or 7201
- **THEN** it is refused and logged and nothing is scheduled

#### Scenario: Wake resource released
- **WHEN** the push fires, is skipped, is cancelled or the fixture detaches
- **THEN** the wake resource is released

#### Scenario: Schedule needs an armed fixture
- **WHEN** the schedule command runs while unarmed
- **THEN** it is refused and logged

### Requirement: Cancellation and detach
A scheduled push MUST be cancelled only by service detach, disarm, an explicit cancel command or a
new schedule command. A protected session closing, a reseed and a peer revocation MUST NOT cancel
it. Detach MUST run as the first statement of the service's session shutdown, before the host is
shut down, and MUST disarm, cancel, release the wake resource and clear the stored host reference. The
release build MUST provide the detach function as a no-op.

#### Scenario: Session closes before the deadline
- **WHEN** the connector pulls and closes its session, a reseed runs, and the deadline passes with another session open
- **THEN** the push still fires to the open session

#### Scenario: Service stop
- **WHEN** the sharing service shuts down while a push is scheduled
- **THEN** the schedule is cancelled, the wake resource released and no push fires

#### Scenario: Explicit cancel and replacement
- **WHEN** a cancel command runs, or a second schedule command runs while one is pending
- **THEN** the first schedule never fires and the cancel or replacement is logged

#### Scenario: Revocation does not cancel
- **WHEN** every peer is revoked while a push is scheduled
- **THEN** the schedule stays and the push is logged `skipped` at the deadline

#### Scenario: Detach order
- **WHEN** the service shuts down its sessions
- **THEN** detach runs before the host is shut down

#### Scenario: Release build
- **WHEN** the release build calls detach
- **THEN** nothing happens

### Requirement: Outcome log
The fixture MUST log one bounded line per outcome under the existing probe tag: seeded, scheduled
(with delay, deadline and process id), fired (per destination session prefix), skipped, cancelled
(with reason), disarmed and expired-omitted (with count), each with an elapsed-realtime timestamp.
Lines MUST carry no identifying data other than markers, event ids, session prefixes and the process id, and MUST NOT carry a key or a full installation id. Refusals (bad seed, bad delay, unarmed schedule, push while armed) are outcomes and MUST be logged.

#### Scenario: Process survival evidence
- **WHEN** a push was scheduled and later fired
- **THEN** the `scheduled` and `fired` lines carry the same process id

#### Scenario: No secrets
- **WHEN** the log lines are scanned
- **THEN** none contains a key or a full installation id

### Requirement: Fixture is debug-only
Every fixture class, action string and wake-lock tag, and any debug-only exact-alarm permission, MUST exist only in the debug source set and manifest. The `WAKE_LOCK` permission is declared in the debug manifest; because a library may already merge it into the release manifest, it is not a release-guard string. New strings outside the existing forbidden prefixes MUST be added to `releaseHookForbidden` and `ReleaseGuardStringsTest` together, and the release guard MUST fail if any appears in the R8 output.

#### Scenario: Release APK guard
- **WHEN** the release guard inspects the minified APK
- **THEN** no fixture class, action string, wake-lock tag or debug-only exact-alarm permission is present
