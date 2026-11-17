## MODIFIED Requirements

### Requirement: Synthetic event source
In debug builds, the phone MUST register a `full`-session application handler that answers
`pull_request` with `pull_events` containing exactly one §8.1 event, while the Gate B fixture is
not armed. While the fixture is armed, the probe MUST NOT answer `pull_request`; exactly one
component answers each pull. The event MUST carry a fresh random marker as the code, merchant
`Veles synthetic`, amount 100 at scale 2, currency `XTS`, and `remaining_lifetime` ≤ 600. It MUST
NOT read real notifications or `OtpMessage` data. It MUST NOT serve as OTP-12's Gate B fixture or
OTP-13's buffer.

#### Scenario: Pull round trip
- **WHEN** the connector sends `pull_request` in a `full` session with a debug phone whose fixture is not armed
- **THEN** the phone returns exactly one synthetic event, and Chrome parses it and sends a
  matching `event_ack`

#### Scenario: No real data
- **WHEN** a real OTP notification is present on the phone
- **THEN** the probe's response still contains only its synthetic event

#### Scenario: Armed fixture owns the pull
- **WHEN** the connector sends `pull_request` while the Gate B fixture is armed
- **THEN** exactly one `pull_events` record answers it and it comes from the fixture, not the probe

### Requirement: Phone-initiated synthetic push
A debug adb command MUST send exactly one synthetic event as `push_event` over the currently
open `full` session while the Gate B fixture is not armed. If no such session is open, the command
MUST do nothing. While the fixture is armed the command MUST send nothing and MUST log that the
fixture owns pushes.

#### Scenario: Push round trip
- **WHEN** the adb push command runs while a session is open and the fixture is not armed
- **THEN** Chrome receives, parses and acknowledges one synthetic `push_event`

#### Scenario: No session open
- **WHEN** the adb push command runs with no open session
- **THEN** nothing is sent

#### Scenario: Armed fixture
- **WHEN** the adb push command runs while the fixture is armed
- **THEN** nothing is sent and the log says the fixture owns pushes

### Requirement: Strict client parsing and acknowledgement
The Chrome development build MUST decrypt each synthetic event and parse it strictly per
§8.1, then send `event_ack` carrying the phone installation id and the event id. If an event
violates a §8.1 bound, Chrome MUST reject the record and end the session. The development build
MUST also provide a separate debug-only `pullAll()` that decodes `pull_events` carrying 0..5 events
and acknowledges each, decoding the whole record before acknowledging any event; a count above 5, a count that does not match the payload or a duplicate event id MUST reject the whole record and end the session with no acknowledgement. The existing one-event pull MUST keep requiring exactly one event, and the production extension bundle MUST NOT contain `pullAll`.

#### Scenario: Malformed synthetic event
- **WHEN** a probe event exceeds a §8.1 bound
- **THEN** Chrome rejects it, ends the session, and shows nothing in the readout

#### Scenario: Multi-event pull
- **WHEN** `pullAll()` receives a `pull_events` record with five valid events
- **THEN** Chrome returns five parsed events and sends five matching `event_ack` records

#### Scenario: Empty history
- **WHEN** `pullAll()` receives `event_count = 0`
- **THEN** Chrome returns no events and sends no acknowledgement

#### Scenario: One-event pull unchanged
- **WHEN** the one-event pull receives a record with a count other than one
- **THEN** Chrome rejects it and ends the session

#### Scenario: Malformed count
- **WHEN** `pullAll()` receives a record whose count is 6, or does not match the payload, or repeats an event id
- **THEN** Chrome rejects the whole record, sends no acknowledgement and ends the session

#### Scenario: Production bundle
- **WHEN** `bundle.test.ts` inspects the production bundle
- **THEN** it contains no `pullAll` code, found through a distinctive string or the development-guard marker so minification cannot hide it

#### Scenario: Bound violation in a multi-event pull
- **WHEN** one event in a `pullAll()` record exceeds a §8.1 bound
- **THEN** Chrome rejects the record and ends the session
