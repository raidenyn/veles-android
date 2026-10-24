# OTP-02 contract-first protocol package

Status: approved by the user on 2026-10-23, including the revocation RFC clarification.

## Intent

Freeze issue #81's protected Bluetooth protocol and Native Messaging contract
before Android, Rust, and Chrome implementations expand. The authoritative
requirements are OTP-02 and the product/security sections of
`docs/rfcs/2026-08-21-otp-external-devices.md`. Desktop and watch share the
phone/client protocol; watch firmware and local PIN behavior are out of scope.

Success means an implementer can determine exact bytes, validation order,
bounds, state transitions, and failures without inventing a wire-level choice.
It also means executable checks distinguish valid and rejected examples and
two independent reviews have no unresolved acceptance-blocking findings.

## Approaches

1. **Contract-first package (selected by the user):** normative document,
   machine-readable schemas and fixtures, executable contract checks, and
   cross-language fixture-consumer skeletons. Keeps protocol implementation in
   its assigned follow-up tasks while exposing inconsistencies early.
2. **Executable reference codec:** additionally implement binary codecs and a
   reusable protocol state machine. Offers earlier implementation feedback but
   expands OTP-02 and risks creating a competing production implementation.

## Goals and constraints

- Preserve the RFC's SPAKE2 suite, roles, identities, code transform, AAD and
  confirmation requirements, attempt limits, and enrollment key schedule.
- Specify installation records, fingerprints, activation and authenticated
  recovery, reconnect signatures and key schedule, protected record encoding,
  fragmentation, capabilities, errors, limits, and downgrade behavior.
- Freeze Native Messaging envelope version 1 independently of BLE protocol,
  binary schema, fixture-format, and negotiated bridge API versions.
- Keep every bridge payload opaque: exactly one complete record in
  `record_base64`, without authenticated fields remapped into envelope JSON.
- Preserve endpoint privacy, memory-only OTP retention, bounded resources,
  strict sequencing, explicit activation authorization, and fail-closed behavior.
- No production crypto, BLE, connector, trust-store, UI, or firmware work.
- No plaintext fallback, spike compatibility, unbounded compatibility window,
  invented successful security-review evidence, or placeholder wire definitions.

If exact specification reveals a conflict with the RFC, document it and obtain
approval for any semantic amendment rather than silently changing requirements.

## Deliverable organization

Proposed paths below are builder choices, not previously agreed user requirements.

- `docs/protocol/veles-v1.md`: sole normative wire contract with a requirements
  traceability table linking each OTP-02 acceptance clause to evidence.
- `protocol/v1/`: versioned machine-readable envelope schemas, binary layout
  descriptors, fixture-format schema, positive/negative fixtures, and a manifest.
- `protocol/checks/`: test-only validation and generation utilities, with a
  documented single aggregate verification command integrated into Gradle.
- Tests under the existing Rust, extension, native-bridge, and Android test
  trees consume the same fixtures; no copied independent fixture truth.
- `docs/protocol/reviews/`: reviewer identities/model metadata where available,
  reviewed revision or content digest, findings, dispositions, and final verdicts.

The RFC governs product and security requirements; the protocol document governs
exact wire encodings within those requirements; schemas and fixtures are derived
artifacts. Any disagreement fails the checks and requires correction rather than
allowing a schema to override normative requirements. Semantic amendments require
user approval and an RFC update in the same change. Fixture changes accompany contract
changes and are versioned in git. Final review applies to the final content.

## Normative contract coverage

### Binary encoding and cryptography

Include an explicit threat model and downgrade policy. Trace plaintext, spike
protocol, unsupported suite/version and compatibility-window rejection to negative
fixtures. Document the RFC's stable installation ID exposure in unencrypted
reconnect hellos as a tracking limitation; this design does not silently change
that handshake. Any identity-hiding redesign requires an approved RFC amendment.

Define every message's tag, field order, width, byte order, exact length prefix,
string rules, enum values, and limits. Define canonical handling of empty,
optional, unknown, duplicate, and trailing data. Distinguish RFC 9382's specified
little-endian lengths from Veles network-order encoding.

Specify the complete SPAKE2 public context and transcript; point validation;
password-to-scalar edge cases; confirmation ordering; enrollment derivations;
installation record and enrollment transcript bytes; and activation MAC inputs.
Specify reconnect hellos, offered/selected versions and capabilities, role-ordered
signed transcript, strict P1363 low-S signature handling, ECDH validation,
HKDF salt/info inputs and outputs, encrypted confirmations, session identifiers,
record headers/AAD, nonce construction, and counter exhaustion behavior.

Define protected pull, response, push, delivery policy, heartbeat, acknowledgement,
and error records, including correlation, retention, duplicate handling and
authorization. A transport write completion never substitutes for application
acknowledgement. Explicitly specify protected blocked-phone-state responses
distinct from empty history; OTP phone/event IDs, receipt time, remaining lifetime,
code, merchant, amount and currency; enrollment `desktop`/`watch` device types;
and the user-visible enrollment fingerprint format. Specify authenticated mutual
removal records and their acknowledgement/disconnect ordering.

Freeze the GATT service and characteristic UUIDs, properties, write/notification
semantics, and fragmentation profile for direct watch clients as well as desktop
bridges. A bare GATT client at negotiated MTU >=185 must work. Host-runnable Rust
fixtures must remain consumable by later watch-crate tests without a browser or
JNI dependency. Define fragmentation independently of authenticated records,
including MTU assumptions, identifiers, timeouts, interleaving, disconnect
cleanup, and all per-peer/global resource ceilings.

### State and lifecycle

Use explicit transition tables: input, current state, validation, permitted
effect, next state, timeout, and failure action. Cover pairing-window and attempt
accounting; confirmation; pending/committed-waiting-ack/active enrollment;
interruption-safe authenticated recovery; cancellation, revocation and expiry;
normal reconnect; key confirmation; records; and teardown.

The user approved resolving the RFC revocation-signaling conflict by preserving
peer-record deletion and generic authentication failures. A failed reconnect
does not prove revocation; the watch shows "Could not authenticate — re-pair
required". The RFC is amended in the same change. No durable revoked-peer
tombstones are added solely to distinguish that failure. Authenticated mutual
removal on an existing authorized connection remains a separate operation.

Recovery must specify what inactive records authorize, durable versus ephemeral
state, replay/idempotency behavior, and how authentication differs from
authorization. No OTP record is authorized before the RFC's activation condition.

### Native Messaging

Define strict JSON parsing (including duplicate keys), framing, byte and field
bounds, request IDs and handle lifetimes, negotiation, every request and event,
correlation, exactly-once terminal completion, unsolicited events, races and
stdout ordering. Define behavior for errors lacking a usable request ID.

Under envelope 1, only `hello` is legal before negotiation; only `ready` or
correlated `error` is returned. Select the highest overlapping bridge API;
flush `incompatible_api` then exit on no overlap. All post-ready messages carry
envelope 1 and the selected API. Explicitly test repeated hello, malformed and
unsupported versions, stale handles, cancellation/link-loss/shutdown races, and
independent phone failure boundaries. Define canonical base64 and byte-preserving
round trips at whole-record boundaries.

Bound outstanding requests, handles, labels, platform hints, advertisements and
discovery-event rates. Define backpressure, output queue/memory ceilings and
overflow handling when the connector renderer is frozen. Account explicitly for
Chrome's host-to-extension Native Messaging message limit with a stricter bounded
envelope. Preserve the RFC's numeric per-peer/global limits rather than choosing
new values for already fixed ceilings.

## Fixtures and executable verification

Every fixture has a stable ID, contract/fixture version, category, inputs,
expected bytes or expected rejection, and expected state/effects where relevant.
Use synthetic identities and OTPs only. Encode byte strings consistently and
large unsigned counters without JSON numeric precision loss.

Include exact encoding and base64 vectors; boundary and malformed cases;
negotiation matrices; ordering/state traces; enrollment/recovery interruptions;
replay, direction, session, tag and sequence failures; and cryptographic
intermediate values needed by later implementations. Derive cryptographic
answers from cited published vectors or independent established primitives,
recording provenance and reproducible generation commands. Do not label an
unsupported cryptographic case verified merely because its fixture parses.

Use published RFC 9382 vectors for SPAKE2 conformance anchors. Generate the
Veles-specific code transform, context/TT serialization, HKDF, confirmation-MAC,
ECDH/signature and AES-GCM expected values with test-only tooling, cross-checking
security-critical derived values through two independent implementations or
published known answers where applicable. A test-only SPAKE2 vector-generation
tool is permitted; it is not an exported codec, production core or a replacement
for OTP-03. Record its implementation/dependency provenance and independent
cross-check. If that cross-check cannot be obtained, mark the affected vector
unverified and keep OTP-02 acceptance blocked rather than deferring its expected
answer silently. Full runtime cryptographic conformance remains OTP-03's work.

Executable checks validate schemas/layout descriptors, bounds, fixture
consistency, exact bytes, base64, negotiation and test-only state traces. They
are deliberately test-only interpreters, not exported reference codecs or
production state machines. Define exact runner/runtime choices during planning,
favoring the existing toolchains and avoiding unnecessary dependencies.

Kotlin, Rust, and TypeScript skeletons must run meaningful shared-fixture loading
and byte/canonical-format assertions now. Future cryptographic implementation
hooks are explicitly identified, not empty passing tests. Report JNI/WASM
cryptographic conformance as deferred to OTP-03; OTP-02 checks do not claim it.
OTP-02 alone does not close Gate A, whose full JNI/WASM execution depends on
subsequent implementation. Run contract checks in CI as well as the documented
local Gradle aggregate. Any new dependencies must satisfy repository pinning,
SBOM and license/supply-chain policy.

## Independent review and acceptance

The user requested a fresh reviewer subagent plus Claude Opus 5.5 in a standalone
session. Provide each the complete contract, fixtures, RFC, and a self-contained
brief. Ask for security, interoperability, missing-encoding, state-transition,
and acceptance-coverage findings. Resolve findings and obtain re-review of
material revisions. Record unresolved limitations honestly. Independence means
fresh review context and no authorship of the reviewed contract. The user is the
acceptance authority for dispositions and final review acceptance; unresolved
security/interoperability findings cannot be silently downgraded by the author.
These reviews do not replace the external security review required by Gate D.

Standalone access was verified using `/home/dev/.local/bin/claude --print
--model opus --permission-mode plan` in this repository. The design reviewer
reported `claude-opus-5-5`. Capture machine-readable model/session metadata for
final reviews when available; a model's self-report alone is not independent
runtime attestation. Do not substitute another model silently.

The design receives a builder-completeness review before user approval. This
does not replace either final protocol/security review. OTP-02 acceptance remains
blocked until both requested final reviews and required checks are recorded.

## Left to the builder

Choose exact filenames, test-only runner implementation, Gradle task names,
schema representation, and fixture-generation libraries within existing pinned
toolchain conventions. Propose all previously unspecified protocol constants
with rationale and subject them to the final reviews. Changes affecting user
ceremonies, RFC security guarantees, or scope require user confirmation.

## Approval and next step

### Design review disposition

A fresh reviewer subagent found nothing important unanswered for planning.
A standalone Claude session reporting Opus 5.5 then requested explicit threat
model/downgrade coverage, GATT/watch requirements, vector provenance, review
authority, bridge bounds, revocation signaling, application fields, precedence,
Gate A status, tracking exposure, CI integration and corrected reviewer access.
These are incorporated above. The user approved the generic-failure revocation
UX clarification and corresponding RFC amendment;
stable-ID exposure is documented as existing RFC behavior, not redesigned.

After builder review and user approval of this design, write the implementation
plan. No product implementation or dependency installation precedes approval.
