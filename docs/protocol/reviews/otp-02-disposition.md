# OTP-02 final fix wave — disposition of the independent Opus review findings

Current status: **TECHNICAL_REVIEWS_APPROVED — USER_ACCEPTANCE_PENDING**.
The final section records the current verdicts and evidence. Earlier sections
are historical fix-wave reports; their pending labels describe those revisions.

Original wave status: **FIX_PENDING_REVIEW**. It records what this wave
changed for each finding of `docs/protocol/reviews/otp-02-opus.md` (verdict
REQUEST_CHANGES). Nothing here is an acceptance record: the controller will
re-review this wave and resume the actual Opus session before any
`reviewed`/`accepted` status may be claimed; no entry in
`protocol/v1/manifest.json` claims either status.

Executed as one coherent wave (no cherry-picking): every Critical and
Important finding is implemented normatively with matching descriptor,
fixtures, checks, oracles, and consumers; the minors were implemented where
cheaper to fix than to argue, and the remaining minor (M1 fragment-size
policy) keeps the builder choice with the interop risk discharged
normatively (tested conservative floor). The evidence inventory was rewritten
honestly (I4); the review brief's scope hashes were regenerated last.

## Critical

### C1 — First post-pairing reconnect class mismatch — FIXED_PENDING_REVIEW

- Normative (`veles-v1.md` §6.2, OTP2-SID-001): the outbound `peer_state`
  byte is now exact per endpoint — the phone sends `0x02` while its record is
  `pending`/`committed_waiting_ack` and `0x01` once active; the client sends
  `0x02` while its record is `pending` and `0x01` as soon as its record is in
  the client active family, INCLUDING the incomplete
  `active_activation_pending` mirror.
- The class-selection rule now evaluates the client upgrade FIRST: a valid
  (AEAD + HMAC) phone confirmation carrying `peer_state = 0x01` is the
  signed+encrypted phone-active proof; the `active_activation_pending` mirror
  is upgraded to fully-`active` before the class is fixed, so the first valid
  post-pairing reconnect is `full` on BOTH endpoints when the phone processed
  the activation ack on the enrollment connection. When the phone is still
  `committed_waiting_ack`, that reconnect is `activation_recovery`
  (OTP2-STA-010b step 5 acknowledges and completes; the NEXT fresh reconnect
  is full). No mid-session upgrade exists on any path (unchanged).
- Descriptor (`protocol/v1/states.json`): the client catch-all row that
  unconditionally assigned `activation_recovery` to `active_activation_pending`
  is removed; explicit rows encode the upgrade-then-`full` decision — with the
  upgrade effects (`record_upgraded_fully_active`,
  `phone_active_proof_received`) preceding `set_class_full` inside the row —
  plus recovery rows for `pending`-state clients (both phone bytes) and for
  the mirror receiving phone `0x02`. The phone's catch-all no longer claims
  the client-only state.
- Fixture (`fixtures/reconnect.json`):
  `st-rec-client-activation-pending-upgrades-on-phone-proof` was rewritten —
  the first reconnect performs the upgrade and fixes `full`; the pull/pull-
  events/ack flow then succeeds on THAT session (the previous expectation of
  an OTP rejection plus a second, trigger-less reconnect is gone; a second
  full reconnect remains in the trace as the ordinary idempotent case).
- Paired role trace added (not only local):
  `st-rec-phone-full-on-first-reconnect-peer-active` (phone role: record
  `active`, peer reports `0x01`, class `full`, pull exchange flows immediately),
  together with the existing `st-rec-ack-lost-active-client-direct-ack`
  (committed phone + active client ⇒ recovery re-ack) and
  `st-rec-revocation-is-generic-preauth-failure`/`st-rec-revoke-disconnects-
  connected-peer` covering the remaining role/record-state combinations.
- Both endpoints' outbound byte is descriptor-encoded and asserted
  (`state.test.mjs` C1 test): client rows cover both active-family states and
  the phone rows cover `active` vs `pending`/`committed_waiting_ack`.

### C2 — Blocked pull wire contract contradictory (0x0201 vs 0x0207) — FIXED_PENDING_REVIEW

- `0x0201` `pull_phone_state` is retired ENTIRELY from v1: removed from the
  record type table (§7.3), the application-record/classification lists
  (OTP2-REC-035), the `full` row of §11.4, `binary-layouts.json`
  (`record_types` entry, `layouts` map, the `protected_record` enum),
  `states.json` (`record_types`, `record_type_directions`,
  `session_classes.full`, `record_effects`, `record_flow`, the phone send
  transition), every fixture (`encoding.json`, `records.json`), and both
  state tests. The doc's "deprecated compatibility-free alias; never
  emitted" wording is gone; the single remaining doc sentence says the tag
  value `0x0201` is unassigned in v1 and a record of any unlisted type
  terminates the session (no alias/lifecycle semantics of any kind).
- `0x0207` `phone_policy_state` is the one policy record: its `flags` byte
  now carries the explicit canonical correlation flag **bit3
  `response_to_pull`** — set ONLY for the blocked policy response to one
  `pull_request`; clear for unsolicited policy notices (full-session start /
  prior-to-eligible-delivery observations). bits4..7 MUST be zero (encoded
  exactly by the flags value enum `0..15`); bit2 eligibility MUST equal the
  policy evaluation (unchanged, binary-checked).
- Settlement is exact (OTP2-REC-031): an eligible `pull_events` response
  settles; a blocked `phone_policy_state` with bit3 set settles; an
  unsolicited notice (bit3 clear) NEVER settles; with no outstanding pull a
  bit3-set record is a STALE REPORT — fully accepted as an authenticated
  record (its policy/eligibility bytes are still the live observation), the
  client's pull bookkeeping never underflows, no state effect beyond
  observing. No client-sent record sets or overrides phone policy (explicit).
- Descriptor and interpreter carry the conditional bookkeeping
  (`record_flow` `dec_if_flag`, `record_effects` `{always, on_flag}`) with
  engine support; fixtures:
  `st-rec-blocked-pull-vs-empty-history` (rewritten: flagged blocked
  response + unsolicited notice + empty-history answer),
  `st-rec-policy-response-flag-settles-pull` (flagged response settles;
  unsolicited does not; stale report neither settles nor underflows — a
  further pull stays allowed on both sides),
  `st-neg-policy-notice-does-not-settle-pull` (second pull after an
  unsolicited notice is refused → the non-settlement is provable),
  encoding fixtures `enc-body-phone-policy-state-response-flag` (`020c`),
  `enc-body-phone-policy-state-reserved-flags` (`0220`, reserved-bit reject),
  `enc-body-phone-policy-state-response-flag-locked-policy` (`0008`), plus
  the retained positive `enc-body-phone-policy-state-always-eligible`
  (`0204`) and negatives (`0304`, `0004`).

## Important

### I1 — Prepare not enforced — FIXED_PENDING_REVIEW

- Original enrollment (OTP2-STA-010a, §11.1/11.2): the phone persists its
  public pending record FIRST, and `send_enrollment_prepare` is a send effect
  of that transition — the phone enters `awaiting_commit` only after the
  prepare went out. An `activation_commit` never reaches
  `committed_waiting_ack` without a sent prepare.
- Client gate: after persisting its pending record and releasing its identity
  record the client enters `awaiting_prepare`; it reaches `ready_to_commit`
  (renamed `verified_prepare_ready` in the descriptor for exactness) ONLY by
  verifying the `enrollment_prepare` MAC (stage `0x00`) of the CURRENT
  enrollment connection. A commit is never sent from `awaiting_prepare`
  (no transition exists — pinned by
  `st-neg-commit-without-verified-prepare`), and a prepare arriving before
  the client's identity release is an ordering violation
  (`st-neg-early-prepare-rejected`); a prepare MAC that does not verify
  erases the incomplete state (`st-neg-prepare-mismatch-erases`, including
  the durable-record erasure rows added for client `pending`/mirror).
- Recovery: `enrollment_prepare` was ADDED to the recovery authorization
  lists (`activation_recovery`, `full_pending_peer` — both were already
  implicitly missing it, the doc's class rows now match the descriptor).
  `K_rec_phone` also protects the recovery prepare (OTP2-CRY-019f) — the
  same stage byte `0x00`, `HMAC(K, ET || 0x00)`: an explicit FRESH-`ET` proof
  over the persisted transcript, bound to this session's signed handshake
  keys. Recovery step 0 (new) requires it before the client's commit;
  the lost-ack case (step 5, phone already `committed_waiting_ack`) sends no
  prepare and no commit (the committed state is re-attested by the signed
  confirmation's `peer_state` byte there). Recovery-class sessions fix the
  class and never send OTP records (unchanged).
- Traces: `st-rec-commit-lost-recovery` (phone, prepare precedes the
  re-commit; complete renumbered to the prepare's sequence slot),
  `st-rec-complete-lost-recovery-client` (client: prepare verified before the
  recovery commit), `st-rec-recovery-commit-mac-mismatch` (prepare still sent
  on that session), and the pairing-side
  `st-en-prepare-sent-after-persist-pairing` pinning the exact effect order
  (persist → compute ET → prepare → commit).
- Class lists both original + recovery: yes — the `enrollment` class already
  carried `0x0104` in the descriptor; the doc's OTP2-REC-035/§11.4 rows were
  corrected to include it, and `0x0104` was added to the two recovery
  classes (doc + descriptor + tests).

### I2 — Code not one-use — FIXED_PENDING_REVIEW

- Normative OTP2-CRY-009a: the pairing code is consumed exactly by the
  phone's successful `cB` verification; the window closes for that
  `pairing_id`, the code is erased, and NO further SPAKE2 execution exists
  for that pairing_id — disconnects after that instant do not resume the
  window (`state.test.mjs` I2 test pins the states), the durable
  `pending`/`committed_waiting_ack` record persists, and continuation is
  exclusively the signed-reconnect recovery above — never a second code
  enrollment (OTP2-STA-012 duplicate guard stays). Before a verified `cB`, a
  disconnect still leaves the window open; each fresh `pB` still consumes its
  non-refundable attempt (OTP2-CRY-008 unchanged).
- The recovery path uses signed reconnects and never reuses the code. The
  combined trace `st-en-code-one-use-window-closed-after-cb` proves: code
  consumed at `cB`; post-code disconnect → window closed (`idle`);
  a subsequent `pairing_request` is rejected; recovery continues via
  st-rec-commit-lost (same pairing, fresh signed session).

### I3 — K-identity abort absent normatively — FIXED_PENDING_REVIEW

- New normative clause OTP2-CRY-003a (`veles-v1.md` §3.4): after the peer's
  point passes validation, a shared element that collapses to the IDENTITY
  (`pB = w·N` phone view / `pA = w·M` client view) aborts the attempt with
  the generic pre-auth failure BEFORE any `Ke`/`Ka`, KDF output, or
  confirmation value is produced, exposed, or transmitted; the
  already-consumed attempt is NOT refunded (OTP2-CRY-008 cross-referenced);
  all partially derived intermediates are erased. PROVENANCE's profile
  choice 2 was rewritten to defer to the now-normative rule for the
  peer-input case (the generated-artifact case remains an engine restart).
- Committed negative vector `veles-neg-shared-secret-identity` (crypto.json,
  also produced by `tools/generate-vectors.mjs` so the candidate/committed
  equality test holds): same code AND both own scalars as the positive base;
  deviation `shared_secret_identity` forces the peer point; BOTH endpoint
  views must abort. Independent proofs: the engine
  (`crypto.mjs` `spake2Exchange` now validates forced peer points and checks
  BOTH views' K) and the Python oracle (its own affine arithmetic collapses
  `K` to the identity); the rejected-proof mapping requires the oracle
  report. New crypto stage `shared_secret`, deviation enum entry, and a
  named check in `crypto.test.mjs` (same code/x/y/pairing_id assertions +
  abort-proof details) — the requirement is evidence-collected in the
  manifest.

### I4 — Evidence overstated proof — FIXED_PENDING_REVIEW

- `protocol/checks/evidence.mjs` was rewritten. The generic
  "loader loads the committed tree" test is no longer attached to any
  requirement as proof. Every requirement now carries typed, closed
  references only: `{kind: "document"}` (its normative anchor),
  `{kind: "fixture"}` (a committed fixture that tags it —
  `fixture:protocol/v1/fixtures/<file>#<id>`), and `{kind: "test"}` (a named
  check that literally names the requirement in its committed source, e.g.
  `test:protocol/checks/crypto.test.mjs#...`). Requirements with NO fixture
  tag and NO named check are explicitly `documentary_only` (traceability
  text, registry entries, product-level constraints, threat-model and
  observability boundaries) — no testable claim is made for them. The
  inventory FAILS on dangling curated test names (and on missing check files
  in the committed tree), so every reference is closed. Final reconciled
  count for THIS revision: 40 of 156 documentary-only (116
  evidence-collected); the scoped re-review's curated-only test crediting
  is the final accounting.
- `protocol/v1/manifest.json`: `evidence_ids` populated from that derived
  inventory (anchor + fixture + test references); entries with fixture/check
  evidence are `specified_evidence_collected` (116); documentary-only
  entries stay `specified_awaiting_controller_review` with only the anchor
  (40). No entry is `reviewed`/`accepted`. The manifest↔inventory equality
  is enforced by `protocol/tools/check-contract-text.mjs` (any drift fails).
- Honesty rules applied while tagging: I did NOT claim fixture coverage for
  broad privacy/static rules with no product implementation (e.g.
  OTP2-SEC-005 observability, OTP2-SEC-001 threat model, OTP2-CON-002
  prohibitions, the PRD-*/WAT-002 product constraints) — those are
  documentary-only by construction; and the two RFC 9382 anchors actually
  exercised (first and fourth vectors) are exactly what §12.1 now claims
  (M6).

### I5 — Incomplete expiry not mapped to Chrome APIs — FIXED_PENDING_REVIEW

- New normative clause OTP2-STA-010d (`veles-v1.md` §5.4): the Chrome client
  maps OTP2-STA-010c to `chrome.storage.session`-resident per-pair continuity
  tokens (memory-scoped: survives worker restarts within one browser-profile
  session; destroyed at browser exit/profile session end/OS logout/explicit
  clearing). On every worker load, missing/unreadable/changed token ⇒
  continuity lost ⇒ incomplete activation material (`active_activation_pending`
  mirror with its public `ET` and activation-pending flag) is DELETED;
  ordinary fully-`active` trust persists in durable browser-profile storage
  and is unaffected; within one profile session the 86 400-second exponent
  applies; conservative discard replaces any boot-clock reconstruction.
- Re-pair consequence is stated exactly: when the client has discarded its
  incomplete record while the phone retains any trust record for the
  installation ID, the client can neither recover (material gone) nor re-pair
  (OTP2-STA-012) — the path is an explicit phone-side revocation first, and
  recovery/permission UX must present it as a phone-side action. The
  duplicate-ID guard is preserved deliberately (no tombstone, no silent
  replacement). The clause is documentary-only honestly (no product
  implementation exists to execute a browser check).

### I6 — Stray debug artifact — FIXED

- `protocol/debug-schema-tmp.mjs` (untracked worker artifact with an absolute
  path) removed. No replacement, no note kept — the tree is clean.

## Minor findings

- **M1 (exact-MTU fragment size)** — builder choice kept (exact
  non-final size = negotiated MTU − 7 is deliberate: maximal packing at any
  negotiated MTU, no per-MTU table); the interop risk is discharged
  NORMATIVELY: the conservative floor constants are now normative and
  tested — `fragment_payload_bytes_at_min_mtu = 178` (`185 − 3 − 4`),
  ≤ 24 fragments per record at that floor (OTP2-GAT-010; `limits.json`),
  with the existing committed fixture `frag-mtu185-max-total-4096` pinning
  the 24 × 178 + 2 chunking of the largest record at MTU 185 exactly, plus
  descriptor/test assertions. Receiver-side behavior was never size-assuming,
  so no change is needed there.
- **M2 (heartbeat 4× wording)** — OTP2-REC-034 rewritten: the terminating
  threshold is exactly "more than 4 heartbeats within any rolling 60-second
  window in one direction" (the 5th inside the window), stated as its own
  fixed pair (4, 60 s), with an explicit sentence that it is not an
  arithmetic restatement of the 30 s spacing (the descriptor/test pins
  {max 4, window 60}).
- **M3 (unknown ready capability)** — OTP2-BRD-010b now separates host-side
  (correlated `error` `invalid_request`) from connector-side: the connector
  has NO correlated-error message to send (no connector→host error request
  exists in v1), so an invalid `ready` fails the negotiation LOCALLY (session
  stops, port closed, local synthesis of outstanding requests). No
  connector-sent error is claimed anywhere.
- **M4 (overflow exit code)** — exit `4` is defined explicitly in the
  normative exit table (OTP2-BRD-032) and referenced from OTP2-BRD-030a; the
  bridge model already closed with 4 and the two overflow tests now assert
  `exitCode === 4` exactly.
- **M5 (manifest/doc statuses + README counts)** — manifest top-level
  `status` text and every entry's status now match the two current statuses
  (116 collected / 40 documentary-only, no review/acceptance claims; the
  earlier wave reported a pre-reconciliation 115/41 and the scoped
  re-review corrected it); the
  doc's §13 status vocabulary matches the manifest's `status_values`; the
  protocol README's entry-count/test-count/fixture-count lines and its
  exit-code wording were refreshed to the current tree (155 Node tests).
- **M6 (RFC anchor counts)** — §12.1 now claims exactly TWO used anchors
  (first + fourth) and says so, naming why the two mixed-empty-identity
  vectors are not anchors (Veles identities are always non-empty); the
  fixture provenance table already named only those two.
- **M7 (vacuous subgroup self-check)** — `oracle.py` `point_mul` no longer
  reduces `k %= N`, so its `N·P == identity` self-checks (M/N/G) and its
  subgroup validation are real group-order computations; PROVENANCE records
  the change and its honest scope (on this cofactor-1 curve the check is a
  redundant-but-real audit, not a security claim).
- **M8 (full_pending_peer / duplicate-ID)** — OTP2-STA-012 explicitly
  includes `committed_waiting_ack` (the guard is over the installation ID
  across every record state). The `full_pending_peer` reachability is stated
  honestly (unreachable through the defined flows) with the class retained
  as a defensive least-privilege bound; its machinery is fully defined
  (prepare-then-commit; a late commit against an active record is an
  idempotent statement duplicate with NO state regression) and exercised by
  the new fixture `st-rec-full-pending-peer-commit-duplicate-no-regression`
  (forbids `record_committed`).
- **M9 (string counting units; oversized OTP fields)** — new
  OTP2-BRD-002a: every envelope string bound is a UTF-8 BYTE length (label
  64, platform_hint 256, bridge_version 64 bytes; error stage 32 bytes —
  ASCII alphabet, so units coincide; request_id ASCII); the test-only
  envelope checks enforce the byte-exact bounds (`checkUtf8ByteBounds`) and
  the derived envelope schema is documented as the outer code-unit shape
  gate only; `limits.json` entries renamed to
  `bridge_platform_hint_max_bytes` / `bridge_error_stage_max_bytes` with the
  unit `utf8_bytes`. For events: OTP2-REC-030 states the oversized behavior
  exactly — an event whose code/merchant/any field exceeds its bound is
  rejected WHOLE at length validation; no truncation, clipping, partial
  delivery, or shortened OTP text (the committed
  `enc-body-otp-event-code-over-max`/`...merchant-over-max` fixtures encode
  it).
- **M10 (OTP-03 hooks)** — all four cross-language consumers
  (`protocol_fixtures.rs`, both TypeScript consumers, the Kotlin consumer)
  now name their concrete future OTP-03 hooks explicitly (real SPAKE2/AEAD/
  record-codec/state-machine conformance over these same committed vectors
  through JNI/WASM/runtime paths), stating that OTP-02 evidence stops at
  the shared-fixture byte-level surface.

## Dispositions of choices accepted as reasonable in the review

Unchanged: eight-byte session-ID AAD prefix with fresh session keys; stable-ID
linkability (recorded limitation); 64 KiB native message cap; bounded
overflow fail-close; UUIDs; 24 h incomplete expiry; heartbeat/idle timers;
SPAKE2 transform/context/TT/Ka isolation/enrollment labels; highest-overlap
negotiation.

## Verification of this wave (run evidence)

- `node protocol/checks/check.mjs` (aggregate): fresh generator run, oracle
  + OpenSSL cross-checks of BOTH the committed and candidate vectors,
  155 Node tests (contract 45, bridge 51, state 32, crypto 27 — 0 failures),
  and `check-contract-text` PASS.
- `JAVA_HOME=$JAVA_ROOT/zulu21 ./gradlew protocolCheck` — BUILD SUCCESSFUL
  (includes the Kotlin, Rust, web-extension and native-bridge shared-fixture
  consumers and the full Node aggregate).
- `npm --prefix verify test` — 131 tests, 0 failures.
- Candidate↔committed crypto equality re-proven (`24 fixtures: 8 positive
  (incl. 2 published anchors), 16 negative`; oracle
  `18 verified, 6 delegated to openssl-check, 0 failed`) after the added
  `veles-neg-shared-secret-identity`.

Per the wave instruction this record claims only FIXED_PENDING_REVIEW; the
controller's re-review and the resumed Opus review decide whether any
finding closes.

---

# Scoped re-review round appended (same Opus 5.5 session) — FIX_PENDING_REVIEW

## NEW-1 (Important blocker) — lost `activation_complete` recovery deadlock — FIXED_PENDING_REVIEW

Root cause confirmed: my recovery step 0 sent the prepare only while the
PHONE's record was `pending`, while a client with a lost `activation_complete`
is still `pending` even though the phone's record is
`committed_waiting_ack` (its commit HAS arrived) — the client waited for a
fresh prepare that the doc-forbidden phone would never send, and the phone
waited for a recommit. Deadlock.

Fix (normative, OTP2-STA-010b step 0 rewritten): the phone sends the recovery
prepare EXACTLY whenever the CLIENT's confirmation reported `peer_state
0x02` — the client is below active-confirmed — WHATEVER the phone's own
record state (`pending` or `committed_waiting_ack`) and on `full_pending_peer`
sessions; when the client reported `0x01` no prepare is sent (that client is
beyond commit and re-sends the ack directly, step 5; a fully-active phone
with that client is a `full` session). Descriptor: the prepare-send row now
carries the exact peer rule (`peer_reported: "0x02"` plus its class list),
with the peer byte observed per OTP2-SID-001 on every received
`reconnect_confirm` (interpreter: `peerReported` bookkeeping + the explicit
`peer_state_reported_*` effects on class-fix rows).

Paired matched traces (both roles, complete-lost):
`st-rec-phone-complete-lost-prepares-then-full-session` (phone
`committed_waiting_ack` + client byte `0x02`: prepare → idempotent-duplicate
re-commit → complete re-send → ack → `active`; then a close + fresh
reconnect confirms the client as `0x01` → class `full`, and a pull flows —
the new full session) and
`st-rec-complete-lost-recovery-client` (extended with the same second-
reconnect tail: upgrade → `full` → pull). The ack-loss fixture now FORBIDS
`enrollment_prepare_sent`/`enrollment_prepare_processed` (the client
reported `0x01`; no prepare). Descriptor pins (state.test.mjs): the
prepare-send guard is exactly `peer_reported 0x02`, and no prepare/commit
flows for an `0x01` client against a committed phone.

## I5 (partial blocker) — durable active-family client mirror must survive restart — FIXED_PENDING_REVIEW

Accepted and rewritten. OTP2-STA-010c/010d now separate the client's record
families exactly:

1. **The active-family client record (`active` AND the incomplete
   `active_activation_pending` mirror with its public `ET`,
   activation-pending flag, fingerprint metadata) is DURABLE**: it persists
   in durable browser-profile storage across service-worker restarts,
   reloads (reloads happen within the same profile — the durable record
   survives; only an explicit revocation or user action removes it), and
   extension updates; `chrome.storage.session` disappearance, worker
   restart, or continuity-token loss MUST NOT delete or downgrade it — the
   previous mapping's restart-induced active-trust loss is gone. The
   descriptor no longer carries any `process_load`/`pending_expiry`
   deletion rows for `active_activation_pending` (pinned by state.test.mjs).
2. **The upper bound on the mirror is the PHONE's authoritative below-`
   active` lifetime** (the unchanged phone-side 86 400-second boot-scoped
   rule — the phone has a real boot clock): when the phone's
   pending/committed record expires or is deleted by continuity loss, later
   reconnects receive exactly the generic unknown-peer path (OTP2-REV-001,
   "Could not authenticate — re-pair required") — no stale mirror can be
   resurrected into trust, and re-pairing is then possible because the
   phone holds no record for that installation ID; where a stale duplicate
   would be refused (the client keeps its mirror while the phone still
   holds a record), the path remains the phone's explicit revocation
   (OTP2-STA-012 guard preserved). The mirror has NO independent
   client-side 24-hour delete deadline.
3. **Genuinely-pending client records (still `pending` — no completed
   commit)** map to a CONNECTOR-OWNED monotonic interval: the connector
   context (the document driving pairing) owns a `performance.now()`-based
   interval from the record's creation reading taken in that SAME context;
   the interval runs exactly `pending_record_expiry_seconds`; on context
   loss the fail-closed rule deletes the pending material (pre-activation
   records authorize nothing but recovery, so cleanup is conservative); no
   new elapsed epoch is ever trusted from a restarted service worker or any
   later context (a fresh context cannot synthesize elapsed time against a
   creation reading it did not take); persisted metadata is conservative
   (creation reading + record state) and no wall clock participates. The
   phone's expiry rules are untouched (real boot clock there). NO promise
   is made that Chrome exposes any boot clock, and the mapping's approval
   is explicitly left to the re-review (this document claims
   FIXED_PENDING_REVIEW, not acceptance).
   New executable fixture: `st-rec-client-mirror-durable-across-restart`
   (continuity loss at worker load ⇒ the mirror is untouched, then the
   signed+encrypted phone-active proof upgrades it and a full pull session
   flows; forbids the deletion effects).

Typo fixed: the doc's "86 400-second exponent" wording is replaced with
"expiry" (OTP2-STA-010d); the mirror's earlier "same 86 400-second bounded
expiry and the same continuity rule" retention claim in OTP2-STA-010c is
rewritten per (1)/(2) above.

## Re-review I3 (nonblocking) — committed forced peer points, honest verification — FIXED_PENDING_REVIEW

- The fixture now commits THE EXACT forced peer points:
  `deviation.value` = pB := `w·N` (0x04…) for the phone view and
  `deviation.value2` = pA := `w·M` (0x04…) for the client view, derived
  from the committed base `w`; the schema allows/asserts both for
  `shared_secret_identity`.
- The ENGINE's check now: parses both committed bytes, validates them as
  real on-curve points, rejects them if they equal the positive base's
  honest peer points (a real-deviation premise), and feeds THEM to the
  forced-peer exchange (no locally generated self-substitution).
- The PYTHON ORACLE independently: parses both committed points, validates
  them (on-curve/uncompressed), verifies the premise against its own
  `w` derivation (`w·N` / `w·M` byte-exact), then computes
  `K = x·(committed pB − w·N)` and `K = y·(committed pA − w·M)` — the
  previous self-subtracted tautology is gone; candidate/committed equality
  re-proven (24 fixtures; the generator commits the same bytes).
- Mutation regression added (`crypto.test.mjs`): mutating the deviation to
  the positive base's honest peer points must FAIL the check ("must differ
  from the positive base") — the abort rule does not fire for a valid
  normal peer point, proving the negative is falsifiable.

## Re-review I4 (nonblocking) — no automatic body-scan crediting; concrete ACC-008 — FIXED_PENDING_REVIEW

- The automatic test-body requirement-ID scan is REMOVED: a mention in a
  comment is not evidence. Test references now come ONLY from the curated
  explicit map (`NAMED_TEST_REFS`), every entry closed-validated against
  actually committed, verbatim test names (dangling references fail the
  inventory).
- ACC-008's generic loader reference is replaced by the concrete named
  checks: the committed negotiation selection matrix (highest-overlap
  recomputed), the committed envelope fixtures (valid accept / invalid
  reject at the declared stage — the compatible/incompatible version
  matrices run through it), the byte-equal candidate/committed vector
  determinism, and the negative-vector binding test.
- Documentary-only count reconciled at the final value: 40 of 156 (116
  collected) in the manifest, this disposition, and the protocol README
  (the first wave's 43/41 pre-reconciliation figures are corrected and the
  report's earlier numbers superseded). No `reviewed`/`accepted` claim
  exists anywhere.

Verification of this round: targeted state suite 33/33 (incl. the NEW-1 and
I5 pins) and crypto suite 28/28 (incl. the I3 mutation regression), then the
full aggregate (`check.mjs`: 157 tests, 0 failures + document consistency),
`./gradlew protocolCheck` BUILD SUCCESSFUL, `npm --prefix verify test`
131/0. Review brief scope hashes refreshed LAST. Nothing here claims
review acceptance; the controller's re-review and the resumed Opus session
remain the acceptance authority.


---

# Independent reviewer-subagent round (`otp-02-subagent.md`) — FIX_PENDING_REVIEW

## R1 (Critical) — cB-to-identity interruption cannot always recover — FIXED_PENDING_REVIEW

Normative (OTP2-CRY-009a, OTP2-STA-010b lead/step 4, §11.1 note rewritten):

- A persisted peer record comes into being only when the endpoint
  AUTHENTICATED the peer's `enroll_identity`; an interruption between the
  verified `cB` and that persist leaves an endpoint with NO usable peer
  record. For it, signed recovery does NOT exist and its reconnect fails
  with the generic unknown-installation path (OTP2-REV-001) — recovery's
  promise is now explicitly CONDITIONAL on usable matching persisted peer
  records on both endpoints; no imaginary recovery is claimed for a
  record-less endpoint, and no product behavior beyond the RFC's explicit
  fresh-pairing/cancel semantics is introduced.
- The recovery path is kept exactly for endpoints whose records exist; the
  code stays CONSUMED in every case (OTP2-CRY-009a; never reused, in either
  path).
- A half-persisted ONE-SIDED `pending` record stays INACTIVE (authorizes
  nothing beyond bounded recovery, which can never authenticate against a
  record-less peer) and is explicitly cancellable/revocable or expires
  (86 400 s with the boot/connector-context mappings), after which a
  same-installation re-enrollment succeeds; OTP2-STA-012's duplicate guard
  is precisely scoped to records actually HELD.
- Descriptor: the `enrollment_protected` connection-loss row is split by the
  durable record state — `record_state_in [absent, deleted]` ⇒ effect
  `connection_lost_no_usable_peer_record_repair_only` (repair-only), while a
  persisted record keeps the recovery-only continuation effect; both
  pair machines gained the idle-cancel no-op, and the durable machines gained
  the exactly-scoped `deleted → pending` re-enrollment row (only when the
  record is genuinely gone).
- Both-role interruption fixtures (the two boundaries):
  - `st-en-cb-disconnect-before-identity-repair-only` (phone; covers the
    phone boundary exactly as stated — the phone has sent its identity and
    awaits the client's): after the disconnect the durable record is ABSENT,
    the old window/code is refused, an explicitly NEW pairing window (fresh
    `pairing_id`/code) re-enrolls the SAME installation; forbids any
    commit-era processing and no second code consumption of the same id.
  - `st-en-client-one-sided-pending-cancelled-then-fresh-pairing` (client;
    the boundary after the phone identity, before the client's own was
    confirmable end-to-end): the one-sided `pending` record persists the
    disconnect INACTIVELY, is explicitly cancelled (durable `deleted`), and
    a fresh pairing succeeds afterward (through the prepare gate).
  - `st-rec-no-peer-record-no-recovery` (the wire-level impossibility): a
    reconnect whose installation matches no persisted record gets exactly
    the generic pre-auth failure — no hello, no signature, no session, and
    no recovery class.
  - No OTP record flows anywhere in these traces (class/state separation
    kept); no identity replacement occurs (each fresh pairing produces its
    own identity/ET, and the stale one-sided record is deleted first).

## R2 (Critical) — mismatched `event_ack` vocabulary — FIXED_PENDING_REVIEW

OTP2-REC-033's contradictory sentence is replaced: an AUTHENTICATED,
well-formed `event_ack` whose references match nothing this peer delivered
is exactly the unknown-event SEMANTIC NO-OP of OTP2-REC-032a — it passes
full authentication, CONSUMES its sequence slot, settles no delivery credit,
mutates no state, and is neither error nor violation; "mismatched" vs
"unknown" is not a rejection boundary anywhere; only AEAD/tag, session/
sequence, or class failures are fatal (OTP2-CRY-017/OTP2-ENC-004). The
descriptor and fixtures were ALREADY in this correct shape
(`st-rec-ack-idempotent-unknown-noop`; sequence-consume + no-op effects
asserted, bad tag/sequence negatives separate) — so only the normative
literal changed plus a wording sweep guaranteeing no "rejected (ignored)"
vocabulary remains for authenticated records.

## R3 (Minor) — attempt-count typo — FIXED_PENDING_REVIEW

The §11.1 note now reads "the five-attempt per-`pairing_id` limit and the
rolling limits … the sixth attempt is refused" (the previous note's
"six-attempt per-ID limit" was a plain typo; the limit itself was always
5 and unaltered).

Verification (this round): focused state suite 33/33 and
`check-contract-text` PASS; the aggregate (`check.mjs`) 157/0 + document
consistency; `./gradlew protocolCheck` BUILD SUCCESSFUL (once each; the
verify/workflow policy suite was not re-run — unchanged inputs). The
review-brief's 50-file scope hash block was refreshed LAST. The normal
reconnect, prepare, upgrade and 0x0207 mechanisms of the accepted-round fix
waves were not re-touched beyond the R1 row split. Status:
FIX_PENDING_REVIEW (user acceptance/re-review remain the authority).


---

# R1 residual round (scoped reviewer follow-up) — FIX_PENDING_REVIEW

Residual found after the subagent round: two of the post-cB interrupt paths
still carried unconditioned "recovery-only" wording.

1. **The successful-cB descriptor row's description** (`states.json`,
   `awaiting_cB` | `pairing_confirm_b` ok) said continuation after ANY later
   disconnect is "recovery-only… never a second enrollment". Rewritten to the
   conditional truth: continuation is CONDITIONAL ON usable persisted peer
   records — signed recovery exactly where the peer's identity was
   authenticated and the durable record exists (OTP2-STA-010b); before that,
   the only path is an explicitly user-opened NEW pairing window (fresh
   `pairing_id`/fresh code — the consumed code is never reused) after the
   one-sided record is cancelled/revoked/expired (OTP2-STA-012).
2. **`enrollment_protected` inactivity_timeout row** was unguarded with the
   same misleading recovery-only description. Split EXACTLY like
   `connection_lost`: `record_state_in [absent, deleted]` ⇒
   `inactivity_timeout_no_usable_peer_record_repair_only` (+ close the
   connection); `[pending, committed_waiting_ack]` ⇒
   `inactivity_timeout_pending_record_continues_via_recovery` (defensive —
   the record exists there only after the client identity was
   authenticated). The awaiting_commit/committed_waiting_ack teardown
   descriptions now state precisely WHY recovery applies there (a persisted
   peer record exists by construction).
3. Normative table cells aligned: the `enrollment_protected` and both
   post-`awaiting_commit` rows' inactivity-failure cells carry the
   conditional (or explicit by-construction) wording; no "recovery-only"
   unconditioned token remains on any post-code teardown/idle path.
4. New fixture `st-en-inactivity-before-identity-repair-only` (phone record
   ABSENT, code NOT reused): the 30 s inactivity timeout fires at the
   pre-identity boundary, repairs via an explicitly NEW user pairing window
   (fresh `pairing_id`/code) and the same-installation re-enrollment
   succeeds; FORBIDs both the held-record recovery effect and the
   connection_lost repair effect (the timeout paths are distinct names).
5. Structural pins (state.test.mjs, R1-residual test): BOTH
   `connection_lost` AND `inactivity_timeout` must split identically on
   `enrollment_protected` by record existence — each path with the exact
   repair-or-recovery effect names, `close_enrollment_connection` asserted
   directly (no tautological `|| true` remains), the closed-window outcome
   asserted for every row, and the cB-row description checked for the
   conditional wording with no unconditioned "recovery-only" claim.
6. `st-en-inactivity-before-identity-repair-only` is pinned in the required
   behavioral-families fixture-ID guard.

Run evidence (commands/counts, honest): `node --test
protocol/checks/state.test.mjs` → 34/34 PASS;
`node protocol/tools/check-contract-text.mjs` → PASS (156 entries,
manifest evidence equality after re-stamp: 116 collected / 40
documentary-only); `JAVA_HOME=/home/dev/.jdk/zulu21 ./gradlew protocolCheck`
BUILD SUCCESSFUL (Node aggregate 158/0 incl. 34 state tests, consumers and
cross-checks green; first attempts failed only from this shell's minimal
environment: missing ANDROID SDK location — fixed by copying the repo-root
untracked `local.properties` (gitignored) — and the Gradle daemon's stale
PATH missing `~/.cargo/bin`, fixed by `./gradlew --stop` + restart with
`~/.cargo/bin` on PATH); `git diff --check` clean. README state counts
corrected to the actual 66 positive / 13 negative. The review brief's
50-file scope hashes were refreshed LAST. No acceptance/approval is
recorded; FIX_PENDING_REVIEW only.


---

# R1 second re-scope (`ses_e917e3343ffelct6W44guU6hZA`) — usable-record persistence boundary — FIX_PENDING_REVIEW

Finding (accepted, controller-verified): the persisted `pending` record REQUIRES `ET`
(OTP2-CRY-019g/010a/019f), but the client persisted and then SEND its own
identity before computing `ET`, and the phone persisted before computing `ET`
(before its prepare). Both orderings could publish a release output (identity /
prepare) while no COMPLETE pending record existed, and a persist failure/crash
after the release would leave a published output backed by no usable record.

Fix (fulfilling the existing persistence requirements — no new semantic RFC
change):

1. **Normative (OTP2-STA-010a + OTP2-CRY-019g + §11.1/§11.2 table cells):** each
   endpoint derives `ET` from the two canonical identity bodies — the peer's
   once its `enroll_identity` was authenticated (OTP2-REC-010/011a), plus its
   OWN body which it already knows byte-exactly — BEFORE atomically persisting
   the COMPLETE `pending` record (`ET` included); only after durable
   persistence success may the phone release `enrollment_prepare` or the
   client release its own identity record. A persistence failure or crash
   before completion publishes NOTHING and leaves NO usable record; such an
   endpoint continues on the no-usable-record path (fresh explicit pairing
   window after cleanup/cancellation/expiry — OTP2-CRY-009a, OTP2-STA-012).
   `OTP2-CRY-019g` now states the invariant directly: a pending record exists
   durably ONLY in its complete form.
2. **Descriptor:** the identity-exchange rows are reordered to
   `compute_et → persist_pending_record → send_enrollment_prepare` (phone) /
   `… → send_enroll_identity` (client), gated on the atomic persist
   (`simulate.persist_ok: true`); dedicated persist-failure rows (both
   pairing machines, plus both durable machines recording the cleanup) are
   `persist_ok: false`-gated and release NOTHING; the durable machines'
   success rows carry the same gate. The descriptor's stale unconditional
   post-code recovery summary (`states.json:5` area) is now conditional on
   usable persisted peer records (repair via a fresh explicit window is the
   other branch — OTP2-CRY-009a).
3. **Fixtures:** every existing success-trace `enroll_identity` step now
   attests `persist_ok: true`, and every order expectation pins
   `compute_et` before `persist_pending_record` before the release;
   three NEW evidence fixtures:
   - `st-neg-client-persist-failure-publishes-nothing` — persist_ok false ⇒
     NO identity release, no pending record (durable `deleted`), the failure
     effect + connection close, and order `persist_failed → erase`.
   - `st-neg-phone-persist-failure-sends-no-prepare` — persist_ok false ⇒
     no `enrollment_prepare` ever sent, no record, durable `deleted`.
   - `st-en-phone-pending-survives-interruption-with-et` — persist_ok true ⇒
     ET-bound COMPLETE record survives the immediate post-persist
     interruption and the machine names the recovery continuation (forbids
     the no-usable-record repair and failure effects).

Descriptor structural pins (state.test.mjs): the ET-first/atomic
persist/release order on both role rows, the persist gates, the failure rows
preceding the generic reject rows, durable failure cleanup rows, and the
conditional descriptor summary.

Run evidence (commands/counts, honest): `node --test
protocol/checks/state.test.mjs` → 35 tests 35 pass 0 fail;
`node protocol/tools/check-contract-text.mjs` → PASS (156 entries; manifest
re-stamped after the new fixture tags: 116 collected / 40 documentary-only);
state trace inventory now 69 positive / 13 negative (README updated);
`JAVA_HOME=/home/dev/.jdk/zulu21 ./gradlew protocolCheck` (PATH incl.
/home/dev/.cargo/bin) → BUILD SUCCESSFUL, aggregate Node 159/0 +
Kotlin/Rust/web/bridge consumers + oracle/OpenSSL cross-checks green;
`git diff --check` clean. The review brief's 50-file scope hashes refreshed
LAST. No approval carries across this change: both the fresh reviewer and
Opus will re-review; this record claims FIXED_PENDING_REVIEW only.

---

# Final controller handoff — 2026-10-24

**TECHNICAL_REVIEWS_APPROVED — USER_ACCEPTANCE_PENDING.** All Critical and
Important findings recorded above are addressed through the final scoped
reviews. Historical pending/request-changes labels are retained as history,
not the current verdict. No issue closure or Gate A/Gate D completion is claimed.

## Actual review evidence

| Reviewer | Final verdict | Identity / verification |
|---|---|---|
| Fresh independent reviewer | APPROVE; R1 persistence and stale summary ADDRESSED; R2/R3 previously ADDRESSED | `ses_e917e3343ffelct6W44guU6hZA`; 50/50 hashes, state35/35, textcheck PASS |
| Standalone Opus | APPROVE (technical only); final persistence correction approved with previous scoped approvals retained | `94bf9517-bdda-4dbb-b4b2-20b27f0fd115`; CLI `claude-opus-5-5`, firstParty, 0 subagents; 50/50 hashes, state35/35 |

See the actual verdict summaries and intermediate findings in
`otp-02-subagent.md` and `otp-02-opus.md`. The old independent-session CLI
resume timed out without a verdict; it is not counted as an approval.

Reviewed scope: exactly the 50 path/digest pairs in `otp-02-brief.md`, excluding
review metadata to avoid self-reference. Final key SHA-256 values:

```text
1af9b54f6bee498e04ab2f6e236bd0d9b928d7e741d711659b94325be6e4c023  docs/protocol/veles-v1.md
4ca9266251b4a550896f02b034fb8cc38418b97559b5591f0fbcddf31889c585  protocol/v1/states.json
8add218ee183897c07fb6a78712ba6bcde04d1ff39dd2bd44c38d21163f59026  protocol/v1/fixtures/enrollment.json
06bfb471321cb1f192b7c42d7c0b4f23797d6645f328354db676709082652892  protocol/checks/state.test.mjs
```

Only review metadata and the progress ledger were updated after these reviews;
no reviewed-content byte changed afterward.

## Controller verification on the final content

Command (fresh, unfiltered output inspected; exit0):

```bash
JAVA_HOME=/home/dev/.jdk/zulu21 PATH=/home/dev/.cargo/bin:/home/dev/.local/bin:/usr/local/bin:/usr/bin:/bin ./gradlew protocolCheck --console=plain
```

- BUILD SUCCESSFUL, 54 actionable tasks (8 executed, 46 up-to-date).
- Node159/159: contract45, bridge51, state35, crypto28; no failures/skips.
- Fresh crypto candidate generation and committed/candidate independent Python
  oracle (18 verified/6 delegated/0 failed) plus OpenSSL (13 verified/0 failed).
- Host Rust8/8; web-extension8/8; native-bridge4/4.
- JVM task up-to-date; inspected final XML: 10 tests, 0 failures/errors/skips,
  timestamp `2026-10-24T15:32:43.419Z` (worker's final aggregate execution).
- `check-contract-text` PASS: 156 manifest requirements, 61 limits;
  116 evidence-collected / 40 documentary-only; no invented reviewed/accepted
  requirement statuses. State fixture inventory69 positive/13 negative.
- Fresh `npm --prefix verify test`:131/131 PASS, exit0.
- `git diff --check`: clean. State JavaScript executed by the suites; this
  continuation changed no TypeScript/Kotlin/Rust implementation files.

## Pending user decisions / nonblocking limitations

Opus explicitly marked these nonblocking; they are visible for acceptance,
not silently downgraded or represented as implemented:

1. Make the disconnect-before-identity fixture self-contained by requiring its
   repair-only and old-window-rejected effects (currently covered by steps and
   structural checks).
2. Align the redundant client duplicate-installation guard between absent and
   deleted paths (phone remains the enforcing authority).
3. Add explicit failed-persist self-loops for durable `deleted` records during
   re-enrollment; currently only the `absent` failure transition is explicit.
4. Name phone failure key erasure as an explicit test effect; normative
   connection-end erasure already applies.
5. Decide whether record-conditional recovery merits an additional RFC note.
   Controller ruling: this clarifies the existing recorded-state premise rather
   than changing authentication/activation requirements; both technical reviews
   approved the correction. If rejected, revise before freezing the contract.

These are contract-only/test-interpreter checks: simulated state outcomes do
not prove production endpoint behavior; full JNI/WASM crypto conformance remains
OTP-03, Gate A remains open, and Gate D still requires external review. Receive-
side heartbeat timing remains outside this interpreter's coverage.

Worktree `/home/dev/repos/veles-android/.worktrees/otp-02-protocol`, branch
`feat/otp-02-protocol`, base/HEAD `e130d9f777d862629f741a2d4931b20ccf93153b`.
All changes remain uncommitted and preserved. No push, issue closure, workspace
deletion, original-checkout modification, or additional scheduled task.

## PR packaging note

The user requested a PR for acceptance review. Staged whitespace verification
found two trailing spaces on an otherwise blank line in `veles-v1.md`; these
were removed without changing protocol text or any executable artifact. The
reviewed document hash above remains the historical review identity. The PR
document hash is `5c578eb7469aaae547328dd5ba1478c91058adbf0a9b124a6c85050e17811aad`,
and the brief records this packaging-only update. Technical review verdicts
apply to the protocol content; user acceptance remains pending on the PR.
