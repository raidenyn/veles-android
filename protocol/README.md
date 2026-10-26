# Veles protocol package (`protocol/`)

Machine-readable and test-support artifacts for the Veles external-device
protocol contract (OTP-02). The normative authority is
[`docs/protocol/veles-v1.md`](../docs/protocol/veles-v1.md); everything here is
derived from it or from the approved RFC
(`docs/rfcs/2026-08-21-otp-external-devices.md`) and can never override either.

## Current status (OTP-02 Tasks 1-7 fix wave — awaiting re-review and user acceptance)

Provided in this revision is test-only contract evidence for Tasks 1-6 plus
the final fix wave for the independent Opus review
(`docs/protocol/reviews/otp-02-opus.md`; per-finding dispositions:
`docs/protocol/reviews/otp-02-disposition.md`). It remains subject to
controller re-review and user acceptance; no artifact claims that Gate A or
Gate D is complete.

- `v1/manifest.json` — requirement inventory, entries
  `{id, document_anchor, evidence_ids, status}` (156 requirements). Entries
  whose committed fixtures tag them or whose named checks exercise them are
  `specified_evidence_collected` with their closed evidence references
  populated; the 40 documentary-only entries (traceability text, registry
  clauses, product-level constraints this contract package cannot exhibit
  without product code) remain `specified_awaiting_controller_review` with
  only their anchor. No entry claims `reviewed` or `accepted`; the
  inventory is derived by `checks/evidence.mjs` and the equality is enforced
  (any drift fails the checks).
- `v1/limits.json` — named integer ceilings with unit, scope, `origin`
  (`rfc` = RFC-fixed value, preserved verbatim; `builder_choice` = a constant
  this contract freezes with recorded rationale), and a stable
  `source_anchor` into `veles-v1.md`. Exactly one value (`sequence_max`) is a
  canonical unsigned decimal **string** because JSON numbers cannot represent
  the u64 maximum exactly; every other value is an integer.
- `v1/binary-layouts.json` (Task 2) — test-only descriptor-driven layout
  inventory: one layout per wire message/frame/body/context of `veles-v1.md`,
  fragmentation constants, the record type/direction table, and the field
  value-class vocabulary (fixture `fields` carry `0x`-prefixed lowercase hex
  for byte/enum fields, canonical unsigned decimal strings for numerics, plain
  strings for text). Point on-curve membership, real AEAD tag verification and
  signature/low-S semantics are Task 5; this interpreter performs format-stage
  checks only and is never a reference codec.
- `v1/fixture.schema.json` (Task 2) — JSON Schema draft 2020-12 with category
  `encoding`/`fragmentation` input and expected shapes; rejected expectations
  name their validation stage and absence of state mutation.
- `v1/fixtures/{encoding,fragmentation}.json` (Task 2) — shared positive and
  negative vectors covering every message layout of `veles-v1.md`, exact
  bounds and bound+1 cases, malformed points/signatures, UTF-8/enum/direction
  violations, trailing bytes, and MTU-185 fragmentation boundaries.
- `checks/{fixture,binary}.mjs`, `checks/contract.test.mjs` (Task 2) — the
  fixture loader (Ajv 2020-12 schema validation; raw-text duplicate-key
  detection), the descriptor-driven binary interpreter
  (`validateBinary`/`validateFragmented`), and `node:test` suite
  (`node --test protocol/checks/contract.test.mjs`).
- `v1/envelope.schema.json` (Task 3) — strict JSON Schema 2020-12 for every
  Native Messaging envelope message (16 message branches, unknown fields
  reject, `additionalProperties: false` throughout); derived from
  `docs/protocol/veles-v1.md` section 10 and never overriding it.
- `v1/fixtures/bridge.json` (Task 3) — bridge fixtures (new fixture category
  `bridge`): pre/hello-only negotiation offers and limits (below/at/above),
  canonical-base64 vectors (zero pad bits valid, nonzero pad bits reject,
  whitespace/url-alphabet/unpadded/double-padding reject, decoded
  5119/5120/5121 byte bounds), host event shapes (correlated vs unsolicited
  `request_id` rules), a negotiation selection matrix, and lifecycle/order
  traces (driven end-to-end) including port-loss synthesis, shutdown
  ordering, disconnect error-before-disconnect, link loss, scan expiry,
  rate/count violations and timeout violations.
- `checks/envelope.mjs` (Task 3) — test-only envelope interpreter and bridge
  model: `validateEnvelope(jsonText, phase, apiVersion)` /
  `validateEvent(text, apiVersion)` (framing length, strict raw-text
  duplicate-key JSON, canonical base64 with pad-bit checks, phase rules,
  highest-overlap `selectApi`), `decodeHostFrame` (1..65536-byte frames,
  strict UTF-8), and `createBridgeHost` — a test-only negotiation/lifecycle
  machine with real output queueing: bounded queue (128 msgs/256 KiB) with
  fail-closed overflow — at the ceiling the host releases every scan, link,
  write and retained state and closes (no dropped-message continuation, no
  dropped-id list); the model exposes a one-shot synthesis snapshot
  covering queued-but-undelivered terminal ids, still-outstanding requests
  and the overflowing request for the connector's local once-only synthesis,
  GATT-write completion gating for `sent` (never misreported),
  outstanding/scan/connection caps, a bounded discovery cache (1024 current
  device handles, FIFO eviction), device_found rate/per-scan counters,
  30 s per-request timeout, busy/unknown-handle/cap errors, live request-ID
  reuse fail-close behavior (including malformed but correlatable input), and
  exit codes 0/2/3 plus 4 for the output-queue overflow fail-closed exit
  (OTP2-BRD-030a/032). `checkBridgeTrace(trace)` independently re-derives ordering
  (start-before-discovery, connected-before-message, error-before-
  disconnect, exactly-once completion, no post-release handle use,
  shutdown-lifecycle-before-completion, completion-last), handle
  introduction/release, request timeouts, discovery rates, and port-loss
  synthesis coverage from the trace steps themselves.
- `checks/bridge.test.mjs` (Task 3) — `node:test` suite
  (`node --test protocol/checks/bridge.test.mjs`): byte framing at the
  65536 boundary, raw duplicate-key texts, negotiation and exit-code
  behavior driven through `createBridgeHost`, every request's terminal
  event type, handle lifecycle, caps/rate counters measured from the host,
  fail-closed backpressure with sustained-pressure and release/id/reservation
  regression coverage, discovery-cache FIFO bounding, base64 round trips of
  bytes that are neither JSON nor UTF-8, plus generic executors over all
  committed `bridge` fixtures (negative traces are checked independently,
  positive traces are driven and their host stream compared byte-for-byte
  against hand-authored expectations).
- `v1/states.json` (Task 4) — test-only declarative transition/state
  descriptor mirroring `veles-v1.md` section 11 (phone pairing, client
  pairing, role-explicit session machine, authorization classes) plus the
  durable trust-record machines of section 5 (OTP2-STA-010/010a/010b/010c).
  Transitions carry guard/permission/effect/next-state rows; numeric ceilings
  are never duplicated — guards reference `limits.json` names. Record-type
  direction and session-class allowlists (OTP2-ENC-020, OTP2-REC-035) are
  declared data. Simulated protocol outcomes in traces (`tag_ok`, `hmac_ok`,
  `sig_ok`, `act_mac_ok`, `peer_state`) are explicit simulated inputs
  (OTP2-FIX-004), not cryptographic conformance evidence (Task 5).
- `v1/fixtures/{enrollment,reconnect,records}.json` (Task 4, fixture category
  `state`) — 69 positive and 13 negative state traces: pairing attempt/rate accounting (five allowed,
  sixth rejected; rolling 300 s and 60 s window boundaries; pA churn per
  connection and rolling; malformed/invalid points rejected before
  consumption; one in-flight execution; window expiry), confirmation-order
  enforcement, identity-record admission (device type, duplicate
  installation), enrollment and recovery interruptions at every
  prepare/commit/complete/ack boundary (including the active-client lost-ack
  direct re-ack), class fixation (recovery-only through teardown), public-only
  durable recovery, pending expiry at the 86 400 s boot-scoped deadline, boot
  continuity loss (incomplete records deleted, fully-active survive),
  revocation as generic pre-auth failure, replay rejection, mutual removal,
  and protected-record negatives (duplicate/skipped/reordered sequence,
  u64-maximum counter with the `counter_seek` fixture-only probe, direction/
  session/type mismatch, bad tag, unknown type, excess length — each
  terminating with keys erased and no counter advance), blocked pull versus
  empty history via the `phone_policy_state` flags bit3 canonical
  `response_to_pull` flag (the blocked response settles an outstanding pull,
  an unsolicited notice never does, and a stale report neither settles nor
  underflows; the retired `0x0201` tag is unassigned in v1), per-peer failure
  isolation, ack idempotency, `app_error` termination, the prepare gates of
  the final fix wave (phone sends `enrollment_prepare` after its pending
  persist; the client may commit only after the session's verified prepare,
  original-enrollment and recovery alike), the one-use pairing code
  (OTP2-CRY-009a: the window closes and the code is erased at the verified
  `cB` — the code stays consumed — and post-code disconnection resumes via
  signed recovery ONLY where usable persisted peer records exist on both
  endpoints; interruptions before the peer identity was persisted require a
  fresh explicit pairing window (R1), with one-sided pending records
  inactive until cancelled/revoked/expired), and
  the first-post-pairing-reconnect rule (client `active_activation_pending`
  sends `peer_state 0x01` and upgrades on the signed+encrypted phone-active
  proof BEFORE class selection, so the first valid reconnect is `full` on
  both endpoints — paired role traces included).
- `checks/state.mjs` + `checks/state.test.mjs` (Task 4) — the test-only
  state/authorization interpreter and `node:test` suite
  (`node --test protocol/checks/state.test.mjs`). `loadStates(root)` parses
  and validates the descriptor (stage=descriptor) against `limits.json`;
  `checkProtocolTrace(trace, states)` returns
  `{accepted, error, final_state, effects}`, recomputing every transition
  independently from `states.json` — declared final states, per-step expects
  and required/forbidden/ordered effects are cross-checked against that
  recomputation and never trusted. Failures are explicit no-mutation results
  (`final_state:null`, `effects:[]`) with stages: descriptor, trace, time
  (non-monotonic trace times and timer events fired before their bound),
  transition, expectation, persisted_state (OTP2-CRY-019g public-only
  enforcement), bounds (canonical u64 decimal strings, event_count 0..5).
  `checkResourceTrace(trace, states)` (same test-only module) models the
  phone-side multi-peer resource ceilings separately: 8 GATT clients, 2 tx/
  8 KiB reassembly per peer, 16 tx/64 KiB global, delivery queues 16 msg/
  16 KiB per peer and 128 msg/128 KiB global, and the 4096-byte record bound.
  Ceilings come from `limits.json`; allocations are bounded BEFORE accounting,
  refusals/releases are reported, and structurally impossible steps reject at
  stage=resource.
- `package.json` + `package-lock.json` + `.gitignore` — exact-pinned test-only
  Node dependencies, installed with `npm ci --ignore-scripts`; this package is
  included in the SBOM, license, install-script, and audit policy checks.


## Verification

Run `./gradlew protocolCheck` for fresh candidate crypto generation, Python and
OpenSSL cross-checks of both committed and candidate vectors, all 157 Node
contract tests, document consistency, and the host-only Kotlin/Rust/web/native-
bridge shared-fixture consumers. It installs each npm package with
`--ignore-scripts` and does not build Tauri, JNI, WASM, or require a device.

The aggregate is OTP-02 evidence only. JNI/WASM cryptographic conformance is
explicitly deferred to OTP-03, so this command does not close Gate A. Task 7
still supplies the required independent reviews and user acceptance.

## Governing relationships

1. **RFC** (`docs/rfcs/2026-08-21-otp-external-devices.md`) governs product
   and security requirements.
2. **`docs/protocol/veles-v1.md`** governs exact wire encodings (bytes,
   bounds, constants, state effects) inside those requirements.
3. **JSON files here** are derived inventories of (2); a disagreement between
   them is a defect that fails the (future) checks and requires correcting the
   derived file — never letting a schema override normative requirements.
4. **Fixtures** change only together with the contract and are versioned in
   git.
5. **Semantic RFC amendments** require user approval and an RFC update in the
   same change (e.g., the approved revocation clarification: post-deletion
   reconnect fails with the generic authentication failure, watch copy
   "Could not authenticate — re-pair required", no revocation tombstones).

## Fixture-consumer contract (binding on later tasks)

- Fixtures live under `protocol/v1/` at the repo root; Kotlin, Rust, and
  TypeScript consumers read the SAME files (no copied truth). The Kotlin
  consumer maps test resources directly to the root directory.
- Fixture bytes are lowercase even-length hex; u64-valued fixture fields are
  canonical unsigned decimal strings (e.g. `"18446744073709551615"`).
- Fixture IDs are stable; consumers reference requirement IDs
  (`OTP2-*` from `v1/manifest.json`), never line numbers.
- Consumers (`rust/veles-crypto/tests/protocol_fixtures.rs`,
  `web-extension/test/protocol-fixtures.test.ts`,
  `native-bridge/test/protocol-fixtures.test.ts`,
  `app/src/test/java/me/nagaev/veles/protocol/ProtocolFixturesTest.kt`) run
  in host mode only; no JNI, WASM, browser, or device runtime is required.
- All validation code is test-only and is never imported by product code.
- Fixtures use synthetic identities and OTPs only.

## Dependency provenance policy (binding on later tasks)

- Any new npm/Rust dependency for checks, schemas, or vector generation is
  exact-pinned, enters the existing lockfiles/SBOM/license/supply-chain
  policy, and is recorded with its source reference here and in
  `v1/fixtures/PROVENANCE.md` (once Task 5 creates it).
- The SPAKE2 vector-generation tool is test-only; it is not an exported
  codec, production core, or substitute for OTP-03.
- Veles-specific crypto expected values must be independently cross-checked
  (two independent implementations or published known answers); without a
  cross-check the vector stays marked unverified and OTP-02 acceptance stays
  blocked.

## Related documents

- Normative contract: [`docs/protocol/veles-v1.md`](../docs/protocol/veles-v1.md)
- Approved design: `docs/superpowers/specs/2026-10-23-otp-02-protocol-contract-design.md`
- Plan: `docs/superpowers/plans/2026-10-23-otp-02-protocol-contract.md`
- Authoritative RFC: `docs/rfcs/2026-08-21-otp-external-devices.md` (includes the approved revocation clarification)

## Fragmentation vectors and manifest evidence (OTP-23, profile revision 2)

`v1/fixtures/fragmentation.json` is no longer hand-maintained: `tools/generate-fragmentation-vectors.mjs`
owns the `frag-r2-*` family and two amended revision-1 vectors (`--check` fails on drift, `--write`
updates the committed file after review). Manifest evidence ids are derived, so
`tools/sync-manifest-evidence.mjs` rewrites them (`--check` is part of `checks/check.mjs`). The
descriptor expression is `min(negotiated_MTU - 3, 512) - 4`; see `docs/protocol/veles-v1.md` OTP2-GAT-010.
