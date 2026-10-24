# OTP-02 Protocol Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the reviewed, versioned Bluetooth and Native Messaging contract and executable evidence required by issue #81.

**Architecture:** A normative document defines the wire contract; derived JSON schemas/layout descriptors and shared fixtures make it checkable. Test-only Node interpreters validate encodings and state traces, while existing Kotlin, Rust and TypeScript tests consume the same fixture bytes. No production protocol code is introduced.

**Tech Stack:** Node >=22 built-in test/crypto modules; existing Vitest, JVM/JUnit and pinned Rust toolchains; JSON Schema 2020-12; Gradle orchestration. Select and exact-pin any necessary schema or vector-generation dependencies before use, following existing supply-chain policy.

**Spec:** `docs/superpowers/specs/2026-10-23-otp-02-protocol-contract-design.md`; authoritative requirements in `docs/rfcs/2026-08-21-otp-external-devices.md`, especially OTP-02.

## Global Constraints

- RFC requirements govern; `docs/protocol/veles-v1.md` governs exact encoding; schemas/fixtures are derived. Semantic RFC changes need user approval.
- Envelope version is immutable `1`; bridge API, binary protocol/schema and fixture format versions are separate. Initial supported sets contain version 1 only; hypothetical version 2 is used only in negotiation tests.
- Preserve `SPAKE2-P256-SHA256-HKDF-HMAC`, fixed A/B roles, RFC identity strings, code transform and enrollment labels verbatim.
- Five-minute pairing window, at most five attempts per pairing ID and rolling five-minute interval; one in-flight SPAKE2 operation and pairing window; at most five fresh pA values per rolling minute.
- Preserve eight clients; two incomplete messages and 8 KiB per peer; 16 incomplete messages and 64 KiB global; queues of 16 messages/16 KiB per peer and 128 messages/128 KiB global.
- Direct GATT clients must work with negotiated MTU >=185; the desktop bridge handles all fragmentation and whole-record `record_base64` transport.
- OTP history: at most five events globally, ten-minute maximum lifetime, no durable history; blocked pull differs from empty history.
- No plaintext, spike fallback, JSON remapping of authenticated fields, production crypto/BLE/UI, or indefinite backwards compatibility.
- Deleted peer records yield generic authentication failure; watch copy is “Could not authenticate — re-pair required”. No revocation tombstones solely for distinguishability.
- Both final reviews are mandatory: fresh reviewer subagent and standalone Claude Opus 5.5. User accepts review dispositions. This does not close Gate A or replace Gate D external review.
- Do not commit or publish without an explicit user request. Checkboxes that mention a checkpoint mean inspect status/diff, not create a git commit.

## Review Focus

1. Malformed pre-negotiation input without a usable request ID must not produce an unsolicited pre-ready response or trigger BLE work (Task 3).
2. An output-blocked connector must not make bridge memory grow without bound or misreport `sent` (Task 3).
3. Reconnect after interrupted activation must not authorize OTP while either required activation condition is absent (Task 4).
4. JSON numbers cannot exactly represent large sequence counters; decimal strings and maximum-u64 cases must survive every consumer (Tasks 2 and 6).
5. Test fixtures must detect wrong crypto outputs rather than merely validate their shape or regenerate their own expected values (Task 5).

## Files and boundaries

- `docs/protocol/veles-v1.md`: normative profile, tables, constants and acceptance traceability.
- `protocol/v1/{manifest,limits,binary-layouts,states}.json`: derived contract inventory, fixed bounds, ordered binary layouts and transition definitions.
- `protocol/v1/{fixture,envelope}.schema.json`: fixture and strict envelope schemas.
- `protocol/v1/fixtures/{encoding,bridge,enrollment,reconnect,records,fragmentation,crypto}.json`: shared positive/negative vectors and traces.
- `protocol/checks/{fixture,binary,envelope,state,crypto}.mjs`: separate test-only parsing/validation responsibilities; never imported by product code.
- `protocol/checks/{contract,bridge,state,crypto}.test.mjs`: Node test suites; `protocol/checks/check.mjs`: aggregate CLI.
- `protocol/tools/`: deterministic generation and independent cross-check tools; `protocol/README.md`: commands, dependency provenance and fixture-consumer contract.
- `rust/veles-crypto/tests/protocol_fixtures.rs`, `web-extension/test/protocol-fixtures.test.ts`, `native-bridge/test/protocol-fixtures.test.ts`, `app/src/test/java/me/nagaev/veles/protocol/ProtocolFixturesTest.kt`: real shared-fixture consumers.
- `docs/protocol/reviews/otp-02-{brief,subagent,opus,disposition}.md`: immutable review evidence and resolutions.
- Root Gradle, app test-resource configuration, npm test lists and CI receive narrow integration changes only.

All tools are test-only. Runtime JSON rejection must inspect original JSON text for duplicate keys before ordinary parsing; JSON Schema alone cannot do this.

## Task 1: Normative contract and requirements inventory

**Files:** Create `docs/protocol/veles-v1.md`, `protocol/v1/manifest.json`, `protocol/v1/limits.json`, `protocol/README.md`.

**Interfaces:** Manifest entries `{id, document_anchor, evidence_ids, status}`; limits are named integer ceilings with units and scope. Later tasks reference stable requirement IDs, never line numbers.

- [ ] Enumerate every OTP-02 scope/acceptance/evidence clause and relevant RFC product/security/watch constraint as a stable requirement ID; mark evidence pending rather than claiming acceptance.
- [ ] Write exact field tables for every handshake and application record: tags, fixed widths, variable lengths, byte order, UTF-8 policy, optional fields, enums, bounds, rejection and state effects. Choose previously unspecified constants with rationale, preserving all RFC-fixed values. No `TBD` or open-ended encodings in the final draft.
- [ ] Specify complete SPAKE2 AAD/TT, confirmation sequencing, identity records and fingerprint display; reconnect transcript and signatures; HKDF labels/inputs; GCM header/AAD/nonces; capability/version negotiation; key erasure and counter exhaustion. Quote RFC 9382 lengths distinctly from Veles network order.
- [ ] Specify application records including blocked-phone state, OTP fields and precise amount representation, lifetime handling, policy, heartbeat, pull/push/acks/errors and authenticated mutual removal. Specify recovery authorization and generic post-deletion authentication failure.
- [ ] Freeze GATT UUIDs, characteristic properties, MTU and fragment framing, resource accounting, timers and disconnect cleanup. Include an explicit threat model, stable-ID tracking limitation and downgrade policy.
- [ ] Check each requirement has an anchor and no normative choice remains implicit; inspect diff as a checkpoint. Subsequent executable checks replace this initial manual consistency check.

## Task 2: Canonical fixture and binary validation

**Files:** Create fixture schema, binary layouts, encoding/fragmentation fixtures, `fixture.mjs`, `binary.mjs`, `contract.test.mjs`.

**Interfaces:** `loadFixtures(root: string): Fixture[]`; `decodeHex(text: string): Uint8Array`; `validateBinary(layoutId: string, bytes: Uint8Array, layouts: object): {accepted: boolean, error: string|null, fields: object|null}`. Fixture shape: `{id, fixture_version:1, category, requirement_ids, input, expected}`. Bytes are lowercase even-length hex; u64 values in fixture field objects are canonical unsigned decimal strings. Rejected fixtures name the validation stage and expected absence of mutation.

- [ ] Add `node:test` cases for malformed hex, duplicate fixture IDs, unknown requirement IDs, absent required fields, trailing bytes, bad UTF-8, wrong endian lengths, exact-bound and bound-plus-one inputs. Assert `"18446744073709551615"` is preserved and `"18446744073709551616"` rejected for u64.
- [ ] Run `node --test protocol/checks/contract.test.mjs`; confirm failures identify missing validator behavior.
- [ ] Implement the fixture loader, schema validation and descriptor-driven binary test interpreter. Use `DataView`/`BigInt` for integers; apply bounds before allocation. Choose an exact-pinned JSON Schema validator compatible with 2020-12 and register its lockfile/SBOM treatment if existing dependencies do not provide one.
- [ ] Add layout descriptors and byte fixtures for all Task 1 messages, including nonzero/high-bit bytes, zero-length optional data, invalid points, signature widths and fragment boundaries at MTU 185. Check descriptor constants against the normative tables and limits inventory.
- [ ] Run the contract suite; require all positive bytes accepted and negatives rejected at their specified stage. Alter one expected byte locally to prove a failure, then revert that deliberate mutation. Inspect diff checkpoint.

## Task 3: Native Messaging schemas and negotiation/event checks

**Files:** Create envelope schema, bridge fixtures, `envelope.mjs`, `bridge.test.mjs`; extend normative bridge section.

**Interfaces:** `validateEnvelope(jsonText: string, phase: "pre"|"ready", apiVersion: number|null): ValidationResult`; `checkBridgeTrace(trace: object): {accepted:boolean,error:string|null}`. `ValidationResult` follows Task 2's accepted/error/fields shape. Schema rejects unknown fields; records remain opaque byte strings.

- [ ] Add failing tests for hello-only pre-ready, highest-overlap selection, no-overlap correlated `incompatible_api`/flush/exit, duplicate hello, wrong envelope/API versions, duplicate JSON keys, invalid UTF-8/base64/padding, oversize framing and every request's terminal event type.
- [ ] Add traces asserting start-before-discovery, connected-before-message, error-before-disconnect, no duplicate completion, no post-release handle use, shutdown completion last, and port-loss synthesized failures. Inputs without correlatable IDs before ready close without unsolicited JSON responses or BLE work.
- [ ] Run `node --test protocol/checks/bridge.test.mjs` and confirm the new assertions fail before implementing checks.
- [ ] Implement strict text validation, schema checks and test-only lifecycle trace validation. Specify exact request/handle limits and reuse rules, device event rates, output queue byte/message ceilings, bounded renderer-freeze behavior, timeout/overflow policy, and host output size below Chrome's ceiling. Add below/at/above-limit fixtures.
- [ ] Assert `sent` means all GATT writes completed, never application acknowledgement; exact base64 round trips include binary bytes that are neither JSON nor UTF-8. Run the bridge suite and inspect diff checkpoint.

## Task 4: Enrollment, reconnect and protected-state evidence

**Files:** Create states descriptor, enrollment/reconnect/records fixtures, `state.mjs`, `state.test.mjs`; complete normative state tables.

**Interfaces:** `checkProtocolTrace(trace: object, states: object): {accepted:boolean,error:string|null,final_state:string,effects:string[]}`. Traces identify synthetic endpoint role, monotonic times, persisted state, event, expected state/effects and authorized record classes.

- [ ] Add failing traces for five attempts allowed/sixth rejected, rolling-window boundaries, valid pB consumption before sensitive operations, malformed points not consuming password attempts, pA churn, role/context mismatch and both confirmation requirements.
- [ ] Add interruption traces at every prepare/commit/activation-complete/ack boundary, including lost final ack, reconnect, replay, cancellation, expiry and revocation. Assert pending and committed-waiting-ack states never authorize OTP, and define persistence/recovery authentication independently of activation authorization.
- [ ] Add protected-record negatives for duplicate/skipped/reordered sequence, maximum counter, direction/session/type mismatch, bad tag, unknown type and excess length; all terminate and erase keys. Add blocked pull versus empty history and peer-isolated failure traces.
- [ ] Run `node --test protocol/checks/state.test.mjs`; confirm failures precede interpreter implementation.
- [ ] Implement test-only transition and authorization checking against the descriptor, with explicit no-mutation validation failures. Ensure cases independently assert the required effects, rather than accepting fixture-declared next states as truth.
- [ ] Run state, bridge and contract suites once together; inspect diff checkpoint.

## Task 5: Deterministic crypto vectors with independent provenance

**Files:** Create crypto fixtures, `crypto.mjs`, `crypto.test.mjs`, `protocol/tools/generate-vectors.mjs`, `protocol/tools/crosscheck-vectors/` with exact dependency manifests/lockfiles, `protocol/v1/fixtures/PROVENANCE.md`.

**Interfaces:** `verifyCryptoFixtures(fixtures: Fixture[]): {verified_ids:string[],unverified_ids:string[]}`; generator writes canonical deterministic candidate files under `build/protocol/`, never overwriting accepted expected fixtures by default. Cross-checker reads those candidates and exits nonzero on any mismatched intermediate.

- [ ] Add failing known-answer tests for RFC 9382 published anchors; six ASCII digits including leading zero; code-to-w, exact AAD and TT; enrollment salt/labels; reconnect transcript/ECDH/HKDF; fixed-width low-S P1363; AES-256-GCM AAD/nonce/tag and wrong-code/context/tag negatives.
- [ ] Run `node --test protocol/checks/crypto.test.mjs`; ensure missing/incorrect expected values fail, not skip.
- [ ] Build test-only generation using established primitives. Select two independent implementations for critical crypto operations (e.g. Node/OpenSSL primitives and an exact-pinned RustCrypto test utility). Confirm the chosen SPAKE2 tool implements RFC 9382's precise profile; do not assume a crate named SPAKE2 is compatible. Record implementations, dependency hashes, source references and all exact generation commands in provenance. Any newly needed tool remains test-only and follows SBOM/license/pin policy.
- [ ] Cross-check Veles-specific SPAKE2 intermediates with an independently implemented or reviewed published-compatible path. If no independent check is available, stop acceptance with explicit unverified IDs; do not label shape checks crypto verification or silently move required expected answers to OTP-03.
- [ ] Compare deterministic generated candidates to committed vectors; mutate a key, label, transcript byte and expected output individually to prove mismatch detection. Revert mutations, rerun crypto suite, and inspect diff checkpoint.

## Task 6: Cross-language fixture consumers and build/CI integration

**Files:** Create four consumer tests listed in the file map, `protocol/checks/check.mjs`; modify `build.gradle.kts`, `app/build.gradle.kts`, both npm package test lists, `.github/workflows/ci.yml`, `CLAUDE.md`, and test-only dependency/lock/SBOM inputs as required.

**Interfaces:** `./gradlew protocolCheck` aggregates Node contract suites, host Rust fixture tests, both TypeScript consumers and the focused JVM consumer. `node protocol/checks/check.mjs` runs all four Node suites and manifest/evidence completeness checks; it exits nonzero for unverified required crypto vectors. Kotlin test resources map directly to root `protocol/v1/`, not a checked-in copy.

- [ ] Add consumer tests that load encoding/crypto fixture IDs from the shared root, decode hex/base64 byte-for-byte and preserve full u64 decimal strings. Assert malformed byte strings and missing fixture IDs fail. Use existing test JSON libraries where available; new libraries are test-only and exact-pinned. Rust consumers are host-runnable with no JNI/WASM dependency.
- [ ] Run each focused consumer to confirm missing resource wiring or assertions fail; avoid adding empty or permanently skipped crypto-conformance hooks.
- [ ] Wire Gradle test resources and focused `protocol*` tasks, npm test-list entries and test-only runner. Keep Node orchestration independent of native Tauri bundling/device availability. Add root `check` dependency and ensure generated files remain under `build/protocol/` and are removed by root clean.
- [ ] Run `./gradlew protocolCheck`; expected all Node, host Rust, JVM and TypeScript contract checks pass. This does not assert JNI/WASM protocol conformance.
- [ ] Add CI provisioning using existing pinned action/toolchain conventions and run the aggregate on PR/master. Ensure changed schema/vector tool dependencies enter existing SBOM/license checks; update policy enumeration if a new manifest root is introduced.
- [ ] Run applicable format/lint/type checks and `git diff --check`; verify CI paths and document the single local command and deferred Gate A obligations. Inspect diff checkpoint.

## Task 7: Independent final reviews, dispositions and acceptance handoff

**Files:** Create review brief, subagent/Opus review records and disposition; update protocol traceability/manifest and README with verified evidence.

**Interfaces:** Review scope is an ordered file list plus SHA-256 content digests excluding self-referential review metadata. Each finding records ID, severity, source, disposition, changed files and re-review verdict; unresolved findings stay visible.

- [ ] Finish the acceptance coverage table: every requirement links to a normative anchor and actual passing fixture/check or review evidence; no placeholders or unsupported success claims.
- [ ] Capture the reviewed-content manifest and exact local verification outputs; prepare a self-contained brief requesting security/interoperability/state/encoding/downgrade review against the RFC.
- [ ] Dispatch a fresh reviewer subagent with read-only access to that manifest, brief and complete artifacts. Do not use a prior implementation author's context for final independent review.
- [ ] Start a fresh standalone Claude session via `/home/dev/.local/bin/claude --print --output-format json --model opus --permission-mode plan` with the same brief. Capture returned session/model metadata where available; require Opus 5.5 and do not substitute another model silently.
- [ ] Evaluate findings against the RFC and implement justified corrections. Ask the user about any new semantic RFC change. Obtain re-review from both reviewers after material changes; record the final reviewed digests rather than implying an obsolete approval applies.
- [ ] Run `./gradlew protocolCheck` and relevant formatting/lint checks after final corrections. Confirm every required review/evidence item exists and all blockers are resolved or explicitly awaiting the user's acceptance.
- [ ] Present findings/dispositions, check results, changed files and limitations to the user for review acceptance. Do not close issue #81 or claim Gate A/Gate D complete, commit, push or open a PR without the required user instruction.

## Self-review coverage

Tasks 1–2 cover exact wire layouts, limits, canonical fixtures and GATT/watch
shape; Task 3 covers the complete independent bridge envelope contract and
renderer-freeze bounds; Task 4 covers state/authorization and interruption;
Task 5 covers cryptographic known answers/provenance; Task 6 provides shared
consumer skeletons, CI and supply-chain integration; Task 7 provides independent
reviews and user acceptance. Review Focus conditions are explicitly assigned
above. Unspecified numeric constants are deliberate builder choices under the
approved design and require rationale plus final independent review, not new
product requirements. No product implementation is authorized by this plan.
