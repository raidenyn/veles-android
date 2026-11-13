# pairing-window-admission Specification

## Purpose
The phone's single SPAKE2 pairing window: its lifetime, the six-digit code, the attempt and per-connection budgets, and the generic rejection that makes every pre-confirmation failure indistinguishable.

## Requirements

### Requirement: Single pairing window with an in-memory code
The phone MUST keep at most one pairing window open. Opening it MUST generate a random 128-bit
`pairing_id` and a six-digit code drawn uniformly from `000000`–`999999` by rejection sampling.
The window MUST last 300 s on a monotonic clock. The code MUST live only in memory, MUST NOT be
sent, logged, persisted or included in any exception text, and MUST be erased when the window
closes. A new window MUST get a new `pairing_id` and a new attempt budget.

#### Scenario: Second window refused
- **WHEN** a window is open and another open is requested
- **THEN** the request is refused and the open window is unchanged

#### Scenario: Window expiry
- **WHEN** 300 s of monotonic time pass after the window opened
- **THEN** the window is closed, the code and any core handles are erased, and a later
  `pairing_request` gets the generic rejection

#### Scenario: Code never observable
- **WHEN** a window is opened and closed in any way
- **THEN** no log line, exception message, `toString`, broadcast or notification contains the code

#### Scenario: Process death
- **WHEN** the process dies while a window is open
- **THEN** the window and code do not exist after restart

### Requirement: Attempt budgets
The phone MUST allow at most 5 attempts per `pairing_id` and at most 5 attempts per rolling
300 s across all pairing IDs. After the per-ID budget is spent the window MUST stay open until
its expiry and any further attempt MUST get the generic failure. A verified `cB` or an explicit
cancel MUST close the window early. The code is one-use: any enrollment failure after a
verified `cB` needs a new window.

#### Scenario: Sixth attempt
- **WHEN** five attempts on one `pairing_id` have been charged and a sixth `pB` arrives
- **THEN** the phone answers `preauth_reject`, disconnects, and runs no SPAKE2 operation

#### Scenario: Rolling limit across IDs
- **WHEN** five attempts were charged within 300 s across one or more pairing IDs, and a new
  window is opened
- **THEN** an attempt in the new window within that interval gets the generic failure

#### Scenario: Cancel and new window
- **WHEN** a window is cancelled and a new one is opened
- **THEN** the new window has a new `pairing_id` and a fresh per-ID budget, with the rolling
  limit still applying

### Requirement: Charging order and no refund
Every valid `pB` MUST be charged atomically against both budgets before any `pB`-dependent
operation, and a charge MUST NOT be refunded. The phone MUST check framing and `pairing_id`,
then validate `pB` as a P-256 point, then charge, and only then run the core `exchange`. The window deadline
MUST be checked before the point validation and again before the exchange. A `pB`
that fails point validation MUST be rejected before charging. A connection that only sends
`pairing_request` or receives `pA` and disconnects MUST consume no attempt.

#### Scenario: Charge before exchange
- **WHEN** a valid `pB` arrives
- **THEN** both budgets are decremented before the core `exchange` is called, including when
  the exchange or any later step fails

#### Scenario: Invalid point is not charged
- **WHEN** a `pB` that is not a valid curve point arrives
- **THEN** the phone sends the generic failure and no attempt is charged

#### Scenario: Connect, get pA, disconnect
- **WHEN** a client connects, sends `pairing_request`, receives `pA` and disconnects
- **THEN** no attempt is consumed

#### Scenario: Atomic charge
- **WHEN** a charge races a cancel or the window deadline, or two charge calls race at unit level
  with one attempt left
- **THEN** the charge is atomic: exactly one outcome is observed, and a charged attempt is never
  refunded

#### Scenario: Deadline boundary
- **WHEN** a `pB` arrives 299 999 ms after the window opened, and another at 300 000 ms
- **THEN** the first is processed and the second gets the generic failure (fixtures
  `st-en-window-deadline-*`)

### Requirement: One SPAKE2 operation in flight
The phone MUST run at most one SPAKE2 operation at a time. A second concurrent execution
MUST be rejected with the generic failure and MUST NOT consume an attempt.

#### Scenario: Concurrent pairing_request
- **WHEN** an operation is in flight and a second connection sends `pairing_request`
- **THEN** the second gets the generic failure and the budgets are unchanged

### Requirement: Fresh pA limits
The phone MUST send at most 1 fresh `pA` per connection and at most 5 per rolling 60 s across
pairing IDs. Exceeding either limit MUST get the generic failure, and MUST be rejected before
any scalar generation or point multiplication.

#### Scenario: Second pA on one connection
- **WHEN** a connection has received `pA` and sends another `pairing_request`
- **THEN** the phone answers `preauth_reject` and disconnects

#### Scenario: Sixth pA in a minute
- **WHEN** five fresh `pA` values were issued within 60 s
- **THEN** a sixth request gets the generic failure

#### Scenario: Rejected before the core
- **WHEN** a request is rejected for a pA limit
- **THEN** the core is called zero times for it

### Requirement: Malformed-input and request-rate limits
Any malformed or invalid pairing frame (frame types 0x01–0x05 only; reconnect frames are out of
scope and MUST NOT share these budgets) MUST get `preauth_reject` and an immediate disconnect
(OTP2-CRY-008, OTP2-ENC-010e); a further attempt is possible only on a fresh connection. The
phone alone enforces two cross-connection budgets: at most 20 malformed pairing connections per
rolling 60 s, and at most 10 `pairing_request` frames per rolling 60 s. Every `pairing_request`
counts toward the request budget, including one rejected as busy, with no window or over the `pA`
limit, and the request budget is checked before the `pA` limit. A reached budget MUST reject the
request at `pairing_request` before any `pA` is issued, MUST NOT close the window and MUST NOT
consume an attempt. The values MUST be added to `protocol/v1/limits.json` with
`origin: "builder_choice"` and consumed by the phone and the protocol checks. This is a
`protocol`-component change and bumps every release component.

#### Scenario: Malformed frame closes the connection
- **WHEN** a pairing connection sends a malformed frame or an invalid point
- **THEN** the phone sends `preauth_reject`, disconnects, and a second frame on that connection
  is never read (fixture `st-neg-same-connection-pb-after-invalid-point`)

#### Scenario: Twenty-first malformed connection
- **WHEN** 20 malformed pairing connections occurred within 60 s
- **THEN** the next `pairing_request` is rejected before any `pA`, the window stays open and no
  attempt is consumed

#### Scenario: Eleventh request
- **WHEN** 10 `pairing_request` frames arrived within 60 s
- **THEN** the eleventh is rejected before any `pA` is issued

#### Scenario: Reconnect traffic unaffected
- **WHEN** malformed `reconnect_hello` frames arrive
- **THEN** they do not consume the pairing malformed budget

#### Scenario: Limits file
- **WHEN** `protocolCheck` runs
- **THEN** the new limit names and values exist in `limits.json` with
  `origin: "builder_choice"` and match the phone constants

### Requirement: Timeouts and window deadline
The phone MUST end a pairing connection after 30 s of inactivity and MUST check the window
deadline before every password-sensitive step.

#### Scenario: Inactivity
- **WHEN** 30 s pass without a pairing frame on a connection
- **THEN** the connection is closed and its core handle released

#### Scenario: Client timeouts
- **WHEN** the Chrome client waits longer than its §11.2 timeout for the next pairing frame
- **THEN** it abandons the attempt, closes its core handle and shows the generic failure

#### Scenario: Deadline mid-exchange
- **WHEN** the window expires between receiving `pB` and running the exchange
- **THEN** the exchange does not run and the generic failure is sent

### Requirement: Generic failure before confirmation
Every failure before confirmation MUST be the generic `preauth_reject` followed by a
disconnect, and MUST NOT reveal which step, budget or code check failed. With no open window a
`pairing_request` MUST get the same generic rejection.

#### Scenario: Wrong code
- **WHEN** the client's `cB` does not verify
- **THEN** the phone sends the generic failure, disconnects, and the attempt stays charged

#### Scenario: Indistinguishable failures
- **WHEN** failures occur from an exhausted budget, a bad point, a closed window and a wrong code
- **THEN** the bytes on the wire are the same `preauth_reject` followed by a disconnect

### Requirement: Core execution cleanup
Each core execution handle MUST be closed on every exit path, including disconnect, timeout,
cancel, service shutdown and exception. The window limits MUST keep use below the core's
capacity of eight executions.

#### Scenario: Handle leak check
- **WHEN** any pairing test finishes
- **THEN** the pairing test base asserts no core handle is open

#### Scenario: Service shutdown
- **WHEN** `SharingService.shutdownSessions()` runs with a window and an execution open
- **THEN** the window, the code and the handle are erased and closed
