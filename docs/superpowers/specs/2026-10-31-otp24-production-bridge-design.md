# OTP-24 production Native Messaging BLE bridge

Status: approved by the user on 2026-10-31; implementation planning authorized.

## Intent and authority

Implement issue #87: an on-demand, headless plain-Rust host providing independent
opaque BLE byte transport to multiple Android phones on Windows and macOS.
OTP-23 (#86, merged through #123) establishes feasibility eligibility only.
Production acceptance must use newly built production binaries.

Authority: `docs/rfcs/2026-08-21-otp-external-devices.md`, OTP-24 and the production
architecture amendment; `docs/protocol/veles-v1.md`, especially sections 9–10;
`protocol/v1/envelope.schema.json` and `protocol/v1/limits.json` are derived checks.
Issue #87's production handoff and fresh-evidence requirements also apply.

## Decisions and approaches

The user selected a **separate production crate**, selectively adapting reviewed
feasibility modules. Alternatives considered:

- Refactor the feasibility crate in place: less initial copying, but entangles
  production with disposable drivers, simulation switches, trace sinks and Tauri twins.
- Rewrite everything: clean separation, but discards proven WinRT/CoreBluetooth
  lifecycle behavior and increases physical regression risk.
- Separate crate with deliberate selective adaptation: explicit dependency and
  release boundary, with some duplication maintained during migration. Selected.

The user also selected **preserving the 4,096-byte GATT profile**. The envelope
parser retains its 5,120-byte decoded ceiling; a valid `send` decoding to
4,097–5,120 bytes receives correlated `oversize_record` before fragment creation
or any GATT write. Zero bytes reject. More than 5,120 bytes reject at envelope
validation. Both rejection layers use the frozen error vocabulary.

This resolves a real specification contradiction: section 10 currently says
records above 4,096 are rejected by the record layer without bridge involvement,
while section 9 explicitly forbids them on GATT. Implementation includes a narrow
normative wording correction and matching fixtures, without expanding the wire
profile, changing the envelope ceiling, or changing Android's GATT maximum.
The alternative, expanding GATT/Android to 5,120, was declined.

## Goals, exclusions and failure conditions

Goals: all seven requests and ten event types; strict negotiation and correlation;
bounded ephemeral state at every queue and OS seam; byte-exact fragmentation and
reassembly; independent phone progress; cancellation and resource release;
deterministic tests plus fresh platform and physical evidence.

Excluded: installation/registration and installer selection (OTP-25), connector
product UI and retry UX (OTP-07), trust/crypto/OTP interpretation, persistence,
network sockets/listeners, background services, autostart and native product UI.
No Tauri runtime or packaging dependency enters the production crate.

Failure conditions include promoting the whole PoC, claiming feasibility evidence
as production evidence, treating `sent` as application acknowledgement, logging
opaque bytes, or allowing a stalled phone to block another phone's writes.

## Source and build boundary

Builder proposal: `native-bridge/host/`, package `veles-ble-host`, binary
`veles-native-host`, pinned Rust 1.98.0 and committed Cargo lockfile. Separate
workspace boundary, matching the existing standalone-crate structure. Use reviewed
exact dependency pins where retained; no dependency on the feasibility crate,
`src-tauri`, shared cryptographic core, Node, or frontend assets.

Library modules expose deterministic core/backend seams for tests. Production
entry point selects only WinRT or CoreBluetooth. Linux runs portable library and
test-driver checks; it must not publish a binary claiming to be Windows. Simulation
and adversarial drivers are separate test targets, never production runtime flags.

Add explicit Gradle `bridgeHostToolchainCheck`, `bridgeHostFormat`,
`bridgeHostLint`, `bridgeHostTest`, `bridgeHostBuild` entry points. Builds use
`cargo build --release --locked`; all targets/cache/products live under
`build/native-bridge/host/`, removed by root `clean` and checked by the clean guard.
Platform CI must run actual Windows/macOS adapter compilation and tests, alongside
portable tests. Add a production-host verification path distinct from existing
Tauri scaffold build/package jobs; existing installer workflows must not label
the scaffold binary as OTP-24 production acceptance.

OTP-24 emits the executable and its checksum and includes it in supply-chain/SBOM
coverage. OTP-25 owns installing it, final native-host registration/origin policy,
and installer migration. Physical acceptance uses narrowly scoped test manifests
pointing at the exact production executable, with existing approved signing
identities and recorded hashes. macOS evidence must use the approved bundle/team
and required Bluetooth metadata; no ad-hoc signing/TCC reset workaround.

## Runtime and ownership

One deterministic coordinator owns protocol phase, request table, handles,
discovery cache, connection state, queue accounting, monotonic deadlines and
stdout event ordering. It never waits for OS BLE operations. Inputs are parsed
requests, bounded backend callbacks, writer delivery notices, terminal I/O signals
and clock ticks. No BLE work begins before successful negotiation.

One raw-stdio reader and one writer own the Chrome pipe ends. Only the writer
emits framed JSON. Keep EOF, truncated frame, I/O failure, output blockage and
explicit shutdown distinct. Terminal signals have a dedicated bounded/control
path and cannot be starved behind advertisements or payload callbacks.

Platform adapters use nonblocking command submission and per-connection workers
or tasks. Scan state is separately owned. Every command/callback has a monotonic,
non-reused internal token; callbacks for removed/cancelled tokens are ignored and
their payload storage released. Backend APIs return bounded typed status, never
raw exception strings or device data in errors.

Per-link serialization preserves fragment order. Different links run independently;
a per-link disconnect or timeout cannot synchronously join another link's worker.
Global shutdown cancels links together and observes one process-wide cleanup
deadline rather than multiplying a close budget by 32 connections. Reviewed OS
close/join behavior is adapted, not replaced with speculative cancellation.

Engineering proposal: allow five seconds from terminal input to process exit,
including at most two seconds for stdout drain within that same deadline. Cancel
all OS work immediately, observe cleanup asynchronously, and use explicit process
exit at the deadline rather than joining blocked stdio/OS workers indefinitely.
Deadline exhaustion is a host failure (exit 1), not successful shutdown; do not
emit `shutdown_complete` unless resource-close completion has been observed.
Record actual OS release separately: process termination alone is not evidence
that the radio released the link within the target. Platform tests must exercise
the forced-exit case and independently measure release.

Memory ownership is explicit for JSON frames, decoded records, queued records,
fragment batches, OS callback copies, partial reassemblies and encoded output.
Use owned payload buffers with best-effort erase on cancellation/drop, and avoid
payload `Debug`/trace formatting. Account for simultaneous copies and capacities,
including the writer's current frame and backend's in-flight batch. OS/browser
copies are outside Rust erasure guarantees; do not claim secure erasure of those.

## Connector contract and lifecycle

Implement `hello`, `start_scan`, `stop_scan`, `connect`, `disconnect`, `send`,
`shutdown`, and the exact corresponding event vocabulary in protocol section 10.
No unsolicited `ready`. Highest-overlap negotiation selects API 1 only.

Strict parser rejects duplicate keys before ordinary interpretation, invalid UTF-8,
unknown fields/types, nulls, noninteger/range violations, noncanonical base64 and
wrong versions. Frame length is checked before allocation. Correlatable invalid
requests receive the specified error; uncorrelatable failures terminate silently
after release. Duplicate live request IDs terminate without a duplicate response.

Each accepted request has exactly one operation-specific terminal completion
while the output is usable. Completion keeps its request ID reserved until the
writer has delivered the full framed event, including a frame held by the writer.
On port failure the connector owns local `port_closed` synthesis; the host does
not fabricate delivery or enqueue duplicate completion events.

Handles are random process-local 16-lowercase-hex values, never reused. Device
hints are OS routing hints, not trusted identity. Prevent concurrent connects to
the same normalized OS target, including pending connects. Discovery cache is
bounded FIFO with connected entries pinned; stale/evicted handles fail explicitly.

Preserve all section 10 ordering rules: scan start before discoveries; connected
before messages; affected request errors before disconnected; no later event for
released handles; shutdown errors then remaining lifecycle events then final
`shutdown_complete`. A `sent` means the complete fragment batch's GATT writes
completed, never peer processing or authenticated delivery.

The observable completion milestone is successful return of every native
write-without-response submission for the batch, respecting OS readiness/flow
control. Windows checks the WinRT result; macOS awaits each reviewed bluest
`write_without_response` operation. CoreBluetooth readiness is not an over-air
delivery acknowledgement: OS-owned transmission may remain after `sent`.
Document this local-OS-completion interpretation in the connector contract and
test both adapters against it; no invented peer acknowledgement is introduced.

Cancellation, timeout or failure after any part of a send could have reached the
OS invalidates the connection. Stop submitting fragments, terminate affected
requests, disconnect, and reject further sends on that handle. Do not reuse it
after a partial write or rely on reassembly expiry to restore synchronization.
The adapter fences cancelled work and tracks closing links until OS release, so
an immediate reconnect cannot race an old worker still transmitting to that phone.

Shutdown stops admission, cancels other pending requests, stops scans, disconnects
links, releases retained buffers and OS subscriptions/resources, emits the final
event when stdout is usable, closes stdout and exits. EOF and silent fail-closed
paths release resources without generating shutdown acknowledgements. A blocked
reader/writer must not prevent bounded process exit. No fallback file/socket sink.

## Numeric bounds and overload

Preserve named protocol limits and test constants against `limits.json`:

| Resource | Ceiling/policy |
| --- | --- |
| JSON frame | 1–65,536 bytes, both directions |
| Decoded envelope record | 1–5,120 bytes; GATT send rejects above 4,096 |
| Outstanding requests | 64; excess correlated retryable `cap_exceeded` |
| Scans / connections | 2 / 32, including pending setup/resource ownership |
| Scan lifetime | 300 seconds |
| Scan ownership | A scan counts toward the 2-scan ceiling until the backend's observed `ScanReleased` (pending/active/closing); a failed scan cleanup never releases and withholds `ShutdownComplete` |
| Discoveries | 64 per rolling second per scan; 1,024 per scan |
| Discovery cache | 1,024 process-wide, connected entries pinned |
| Request timeout | 30 seconds; scans use lifecycle rules |
| Output | 128 messages / 256 KiB; writer-held frame included in accounting |
| Send storage | 16 records / 16 KiB per link; 128 records / 128 KiB global |
| Incomplete inbound records | 2 / 8 KiB per peer; 16 / 64 KiB global |
| Fragment inactivity | 5 seconds; collision policy remains section 9 |

Send storage includes pending and in-flight records. Extra backend fragment copies
need separate explicit bounded accounting, not an unbounded downstream queue.
Approved ingress ceilings (2026-11-01 user approval; supersedes the earlier 128 / 64 KiB
global and 16 / 8 KiB per-link builder proposal): input frames <=64 / 256 KiB; backend data
events <=1,536 / 512 KiB global and <=48 / 16 KiB per link, each fragment <=512 bytes.
These are **ingress staging** ceilings (unconsumed OS notifications waiting for the
deterministic core), not reassembly or delivery caps: the frozen incomplete-inbound caps
(2 transactions / 8 KiB per peer, 16 / 64 KiB global, 5 s inactivity) and the send-storage
caps are unchanged and independent. Derivation and guarantee:

- The global ceilings are exactly 32 links x the per-link ceilings (events and bytes), so all
  32 links can sit at their own cap at the same time. Committed and in-transit (deferred)
  fragments share the one global counter, so the ceiling holds across both paths.
- The event and byte ceilings are independent: 48 x 512 B = 24,576 > 16 KiB, so
  512-byte fragments reach the byte cap at 32 events, while small fragments reach the 48-event
  cap first.
- Guarantee (stated for a conforming, uniformly fragmenting peer, not for arbitrary traffic):
  a link may hold an *unconsumed* backlog of 48 fragments / 16 KiB. At the minimum 185-byte MTU
  (182-byte fragment frames) one maximum 4,096-byte record is 24 fragments, so **exactly two
  maximum records (48 fragments, 8,736 bytes) fit while the core is stalled and nothing is
  drained**; at larger MTUs a record needs fewer fragments (MTU 247: 18), so two records fit
  with room to spare. Three records, or any number of tiny records, can exceed the bound; a
  link that exceeds it fails closed (one `LinkLost`, never a gap) while other links proceed.
  The core's prompt wake-and-drain (OTP-24 review I2) keeps the steady-state backlog far below
  the bound; the bound is the burst margin for a stalled core.
- No hardware guarantee is claimed. Platform adapters have their own, smaller upstream
  queues: on macOS the vendored bluest peripheral event channel is a 16-slot overflow-dropping
  broadcast (shared by *all* peripheral events, not only notifications); an overflow there is
  surfaced as a loss and ends the link fail-closed. It is an independent bound on a different
  stall: the 16 slots bound only the macOS link worker thread's poll latency (the worker
  forwards into the gate/ingress without waiting for the core), while the ingress ceilings bound
  core-drain latency, so a core stall still gets the full 48-fragment margin on macOS. The
  16-slot buffer is shared by all of the peripheral's events (write-ready, RSSI, ...), so
  bidirectional bursts shrink the notification margin; measure on hardware and plan a
  separately reviewed vendored capacity patch if needed. That vendored constant is
  deliberately unchanged here (patch provenance). Windows callbacks are not queue-limited by
  the OS adapter but have not been measured on hardware either.
- The ingress is non-blocking: a busy ingress lock defers the event into a bounded, strictly
  ordered in-transit lane (fixed capacity: global event ceiling + 256 control slots) that every
  later lock holder drains first; a callback admission gate has its own bounded lane of the same
  per-link size (48 events / 16 KiB / 8 controls). Memory is fixed-capacity plus the accounted
  payload bytes (see `native-bridge/host/tests/ingress_memory.rs`).
- Separate bounded control/completion slots are reserved per accepted operation/link so noisy
  data cannot displace cancellation. Advertisements coalesce/drop at bounded discovery ingress.
  If a link's data budget overflows, fail that link rather than silently discard stream bytes;
  process-level unrecoverable control failure terminates the host. These ceilings are listed in
  `protocol/v1/limits.json` (`bridge_data_events_*`, `bridge_gate_staged_*`,
  `bridge_deferred_controls_max`) and enforced by the Rust conformance test.

When send storage is full, reject the new request with correlated retryable
`cap_exceeded` before storing/submitting its record; preserve existing link work.
Do not create an unaccounted waiting queue. Terminal events use the bounded output
queue when available, not a pre-reserved output slot for every accepted request;
exhaustion follows the required output-overflow exit instead. Admission always
reserves request/backend control bookkeeping before side effects.

Output overflow is the protocol's process-wide fail-closed exit 4: no further
output admission, no continuing after dropping/reordering an event. Stop work and
release resources; pending connector requests resolve locally on port closure.
Use documented exit 0/2/3/4 paths; other internal/runtime failures are host failures,
never new wire-level error codes. No automatic transport replay/retry: restoration
permits a fresh request, preventing duplicate opaque writes.

## Android GATT boundary and platform adapters

Retain revision-2 UUIDs and write-without-response/notify profile, setup ordering,
MTU floor 185 and ceiling 517, four-byte uniform header and transaction rules.
Single link-budget formula: `attribute = min(ATT_MTU - 3, 512)`;
`payload = attribute - 4`. Exact multiples have a full final fragment and no empty
trailer. Reassembly validates index, total, final size, timeout, collision and caps.
Connector sends/receives complete opaque records only.

Windows reports ATT MTU via WinRT. macOS's maximum write-without-response length
is not assumed equal to MTU minus three: retain the reviewed proof-based policy
(>=512 establishes the common clamped budget; below that refuse undetermined
capacity). No guessed chunk size or new negotiation extension.

Freeze the derived fragment budget for a connection. If a later observed capacity
change invalidates that budget, cancel affected work and close the connection;
never refragment an in-flight record. Capacity changes preserving the identical
derived budget (e.g. within the 512-byte clamp) need no wire change. macOS checks
the reported write capacity before each batch and native write failures take the
same fail-link path. No unsupported capacity observation is invented.

Android transport behavior remains wire-compatible. Shared boundary fixtures must
prove agreement with `GattTransport.kt` and `SharingGattServer.kt`, including
setup callbacks, CCCD ordering and malformed/oversize fragment handling.
Synthetic fixture or test-harness adjustments are permitted for fresh evidence;
Android production changes require a demonstrated interop defect, not incidental
refactoring. Preserve installed app data and approved signing certificates.

Classify deliberate radio-off, no-adapter and service access-denial conditions
using safe bounded stages and protocol error codes. Retryability follows reviewed
W-10 behavior; restoration/new-request recovery is tested. Do not relabel all old
AccessDenied captures as the same root cause. No in-process unbounded retry loop.

## Explicit reuse/adapt/replace map

All paths below are relative to `native-bridge/feasibility/src/` unless stated.
“Reuse” means selectively port with attribution and production regression tests,
not link the disposable crate or assume its verdict transfers.

| Existing module | Disposition | Production work |
| --- | --- | --- |
| `framing.rs`, `strict_json.rs`, `base64.rs` | Reuse | Preserve bounded strict behavior; port adversarial fixtures |
| `protocol.rs` | Adapt | Exact production types; size distinction and schema conformance |
| `fragment.rs` | Adapt | Preserve revision 2; explicit buffer ownership/erase and budget checks |
| `outqueue.rs` | Adapt | Include writer-held ownership and completion-ID reservation |
| `core.rs` | Adapt | Separate protocol/lifecycle concerns where useful; audit all bounds and races |
| `backend.rs` | Adapt | Typed bounded submission, cancellation and cleanup completion seams |
| `runtime.rs` | Replace wiring | Retain single writer/deterministic core; replace 4,096-item mixed input queue with byte-aware ingress and priority terminal signals |
| `winrt.rs`, `winrt_map.rs` | Adapt | Reviewed WinRT operations/classification; per-link nonblocking teardown and bounded copies |
| `macos.rs`, `macos_map.rs` | Adapt | Reviewed CoreBluetooth operations/capacity policy; task/callback ownership audit |
| `closejoin.rs` | Adapt | Bounded OS resource closure, process-wide deadline and survivor progress |
| `host.rs`, `identity.rs`, plain binary entry | Replace | Production metadata, platform dispatch, raw stdio; no PoC IDs or simulation flags |
| `trace.rs` | Replace | No file/environment trace sink in production; safe bounded stderr diagnostics only |
| `sim.rs`, `unsupported.rs` | Test-only adaptation | Dedicated test drivers; no shipping backend selection |
| Tauri twin, frontend, probe/inspection scripts | Exclude runtime | Selectively adapt inspection drivers as external verification tools |
| Feasibility `tests/` | Adapt | Production imports and new gap/race/overflow cases; retain original evidence |
| `native-bridge/src-tauri/` | Exclude | Earlier scaffold, not production runtime or packaging dependency |

## Verification and evidence

1. Portable Rust: formatting, Clippy with warnings denied, unit/integration tests;
   strict envelope/schema fixtures, request-ID reuse while writer blocked,
   exact-once/error ordering, stale callbacks/handles, deadline/overflow paths.
2. Transport: byte-exact base64→fragment→fake GATT→reassembly→base64 properties;
   0 rejects, 1, chunk-minus-one/exact/plus-one, multiples, 4,095, 4,096,
   4,097 and 5,120 reject without writes, 5,121 parser reject; MTU 185 through
   517 and macOS capacity policy; hostile fragments and reassembly budgets.
3. Concurrency: two independent targets with stalled/failed/setup-cancelled peer,
   survivor progress, full queues, disconnect/send races, late callbacks,
   shutdown while requests and partial records exist. Assert resource counters.
4. Property/fuzz: reproducible seeds and bounded corpus runs for strict JSON,
   base64, fragmentation and state sequences; record tool/revision/budget/results.
   Fuzz harnesses are verification-only, not release dependencies.
5. Process: clean EOF, truncated-header/payload EOF, explicit shutdown, broken and
   frozen stdout, input flood and capacity excess. Observe final output, pipe
   closure, deadline, return code and absence of retained OS resources.
6. Native Windows/macOS: actual adapter compilation/testing and fresh synthetic
   two-phone byte transfers. Repeat ready/scanning/transfer/teardown inspection
   for windows, filesystem/registry writes, network/listeners and resource hygiene.
   The Windows Chrome never-visible wrapper exception remains narrowly stated.
7. Chrome-owned lifecycle cases: literal `port.disconnect()`, connector tab close,
   Chrome-owned pipe closure, and shutdown are separate from direct-launch EOF
   diagnostics. Use active scan/two byte-proven links and bind phone observations.
   **Pending-request evidence (owner decision, option 2, 2026-11-02):** an abrupt EOF
   (`port.disconnect()`, tab close, Chrome-owned pipe closure) is silent cleanup — the
   connector is detached and Chrome owns the pipe, so the host's per-request terminal
   events cannot be observed without altering provenance. Those cases verify process
   exit and OS BLE link release for both phones and record each in-flight request's
   outcome as **unknown / not observable**; they neither require nor claim host
   correlated cancellation. The connector's synthesized `internal`/`port_closed` is local
   only and is never evidence about the host. Only **explicit shutdown** (correlated
   `internal`/`shutdown` error for an armed pending request, before `shutdown_complete`)
   and **per-phone link failure** (`send_failed`/`link_lost`) require a proven-pending
   request and a correlated, non-synthesized host error specific to that trigger.
   `cases.json` (`pending_request_handling`) and `validate-evidence.py`
   (`check_pending_handling`) encode the distinction; an EOF record that asserts a
   per-request outcome is a FAIL.
8. Error evidence: deliberate radio-off, no-adapter and controlled service denial,
   then restoration/fresh-request recovery. Record native codes privately in raw
   synthetic evidence, safe classifications in host events and payload-free logs.

Each physical verdict records source/evaluator revision, binary/package hashes,
signing identity, OS/Chrome/account/adapter context, source/phone binding, controlled
actions/timestamps, callback codes, captures, checksum catalog and separate
adjudication. Preserve failed setups. Test fixtures can persist synthetic evidence;
the production host cannot persist payloads or trust state.

Acceptance stays open if required hardware, permissions, signing identities or
platform access are unavailable. Do not inherit OTP-23's waivers as OTP-24 passes.
Include production dependency SBOM and required repository checks for touched
components. Before any PR, run the mandatory Android lint gates and raw-Log guard.

## Builder discretion and approval boundary

### Independent builder check

Kimi k3.1 Pro reviewed this design in session `ses_e6f49fc21ffe0StEjmMEd9yRHt`.
Its questions concerned partial-write cancellation, native `sent` milestones,
macOS signing vehicle, cleanup deadline, send admission and capacity changes.
The technical policies above resolve five as explicit engineering proposals.

The user accepted the recommended macOS test vehicle: reuse the approved PoC
signed `.app` identity as a clearly labeled test vehicle containing the production
host binary. A production-identity migration is deferred to the packaging work.
`docs/desktop-ble-poc.md` explicitly says the PoC identity is not evidence for the
final `me.nagaev.veles.bridge` production identity. Reusing the approved fixture
does not establish final-identity/TCC acceptance. Fresh results must identify the
production source/binary and test-bundle identity separately. No signing migration
is implicit in this design.

Builder chooses internal module split, synchronization primitives, exact bounded
queue implementation, property-test framework and fixture organization, provided
the above semantics and limits remain testable. Reuse existing pins and repository
conventions before adding dependencies. No new UI, persistence or installer design.

User decisions so far: separate production crate; 4,096-byte GATT limit with
explicit oversize rejection; existing approved macOS signed test vehicle with
final production-identity validation deferred. Paths, executable/task names and ingress ceilings
above are proposed engineering calls, not previously user-approved decisions.
This document requires user approval after independent Kimi k3.1 Pro builder
review. Then create the implementation plan. GLM-5.3-Flash performs delegated
coding; Kimi k3.1 Pro performs reviews using their exact available model IDs.
