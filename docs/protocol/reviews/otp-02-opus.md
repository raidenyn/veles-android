# Independent Opus review — first pass

Verdict: REQUEST_CHANGES. Review performed in a fresh standalone Claude Code
session, read-only plan mode, no agents spawned. Session:
`94bf9517-bdda-4dbb-b4b2-20b27f0fd115`. CLI JSON `modelUsage` reported
`claude-opus-5-5` / canonical model `claude-opus-5-5`, firstParty provider.
All50 scope hashes in the then-current brief matched. This is not approval of
later revisions. User acceptance remains pending.

## Critical

**C1 — First post-pairing reconnect class mismatch.**
`veles-v1.md:918-919,786-792,1710-1712`; `states.json:1386,1468`;
`reconnect.json:535` (`st-rec-client-activation-pending-upgrades-on-phone-proof`).
Client active_activation_pending is always assigned activation_recovery even
when phone proves active; phone assigns full. Client's outbound peer_state is
undefined. Existing fixture expects OTP rejection and a second reconnect with no
defined trigger. Fix: define client active_activation_pending outbound0x01 and
upgrade based on verified phone-active confirmation BEFORE class selection;
first valid post-pairing session full on both endpoints. Add two-sided trace.

**C2 — Blocked pull wire contract contradictory.**
`veles-v1.md:1029,1035,1102-1105,1194-1196,1212,1711`;
`binary-layouts.json:296`; `states.json:149-155,175-185`.
0x0201 pull_phone_state is called never-emitted/deprecated yet has layout,
send transition and pull settlement. Allowlists omit0x0207 phone_policy_state.
0x0207 is both unsolicited notification and blocked response with no correlation.
Fix: remove0x0201 everywhere, use0x0207 for blocked policy response and distinguish
solicited response from unsolicited policy notice (type or explicit response
flag). Correct allowlists and outstanding-pull bookkeeping.

## Important

**I1 — Prepare not enforced.** `veles-v1.md:1191-1193,1203-1206,1709,
1651-1653,1670-1671`; `states.json:455,968`. Phone moves awaiting_commit
without send_prepare; client ready_to_commit has no prepare-verified guard.
Add phone send effect, client awaiting_prepare state and lost-prepare trace.

**I2 — Code not one-use.** `states.json:698-730`; `veles-v1.md:1656-1659`.
Disconnect after cB/pending/committed returns window_open and permits second
enrollment with same code, contrary RFC one-use requirement. Close window/erase
code on verified cB; later recovery uses signed reconnect, never reused code.

**I3 — K identity abort absent normatively.** `PROVENANCE.md:182` versus
`veles-v1.md:264-268`. Define nonrefundable valid-pB consumption followed by
generic abort before confirmation; add fixture.

**I4 — Evidence overstates proof.** `evidence.mjs:5,32` gives every requirement
doc plus generic loader test;50/152 have no tagged fixture, all manifest
evidence_ids empty. Do not present generic test existence as security proof.
Map actual named checks or mark documentary-only/traceability-only explicitly.

**I5 — Incomplete expiry not mapped to Chrome APIs.** `veles-v1.md:772-797`.
Chrome lacks boot-token/boot-monotonic API. Define realizable platform mapping
(e.g. session-scoped incomplete trust with conservative continuity loss) and
recovery UX when client discards active_activation_pending but phone retains
active record: phone-side revocation required before re-pair same installationID.

**I6 — Stray debug artifact.** `protocol/debug-schema-tmp.mjs` untracked,
not ignored, contains absolute path; remove worker-created artifact.

## Minor

- M1: exact-MTU nonfinal fragment size is an interop risk; consider allowing
  bounded short fragments consistently (builder choice, not RFC conflict).
- M2: heartbeat4x wording arithmetic incorrect; threshold4/min is consistent.
- M3: unknown ready capability cannot elicit connector-sent correlated error,
  since connector API has no error request; fail/close locally.
- M4: define overflow exit4 explicitly in normative exit table.
- M5: align manifest/doc statuses and stale README counts/future-check wording.
- M6: doc claims4 RFC9382 anchors while only2 used; make honest or add other2.
- M7: Python point_mul reduces modN, rendering N*P subgroup selfcheck vacuous;
  remove claim or use unreduced multiplication; cofactor1 curve unaffected.
- M8: full_pending_peer possibly unreachable; duplicate-ID protection must
  explicitly include committed_waiting_ack.
- M9: JSON string character counting unit undefined; define Unicode unit and
  byte limits. Define oversized merchant/code behavior without OTP truncation.
- M10: consumer tests should name concrete future OTP03 crypto-conformance hooks.

## Reviewed choices accepted as reasonable

Eight-byte session-ID AAD prefix with fresh session keys; stable-ID linkability;
64KiB native message cap; bounded overflow failclose; UUIDs;24h incomplete expiry;
heartbeat/idle timers. SPAKE2 transform/context/TT/Ka isolation/enrollment salt and
labels consistent with RFC9382. Highest-overlap and no-overlap bridge negotiation
correct. These observations do not override the REQUEST_CHANGES verdict.

## Scoped re-review, same Opus5.5 session

Verdict: REQUEST_CHANGES. CLI metadata again reports claude-opus-5-5. Refreshed
scope hashes matched. Reviewer ran state suite32/32 and Python oracle committed
fixtures18 verified/6 delegated/0failed with reports outside build tree.

C1 and C2 ADDRESSED; I2 and I6 ADDRESSED; original enrollment I1 ADDRESSED but
recovery PARTIAL. All M1–M10 addressed as reviewed builder choices/fixes.

### NEW-1 Important blocker — lost activation_complete recovery deadlock

OTP2-STA-010b step0 says phone sends prepare only when phone pending, not when
committed_waiting_ack. Client pending (complete lost) requires fresh prepare
before recommit, while committed phone waits recommit before resending complete.
Descriptor has unguarded prepare-send contrary doc; existing client trace assumes
prepare from doc-forbidden phone. Fix phone prepares whenever client reports
below-active0x02, whether phone pending or committed; add matched phone/client
complete-lost recovery traces.

### I5 partial blocker — durable active client mirror must survive restart

Chrome storage.session mapping exists, but performance.now resets on worker
restart; boot24h clock not available. Discarding active_activation_pending on
browser restart/update adds trustloss absent from RFC. Preserve active-family
client mirror durably and let phone authoritative pending lifetime govern recovery;
document implementable connector timer mapping for genuinely pending records.
Replace typo exponent with expiry at doc lines901/915.

### Nonblocking cleanups still requested

I3 oracle forced-Kidentity currently subtracts wN from itself (tautological),
fixture lacks exact forced peer point bytes needed OTP03. Commit forcedpA/pB and
have independent oracle parse them and derive x*(pB-wN)/y*(pA-wM).
I4 automatic testbody requirementID matching can count comments as evidence;
ACC008 generic loader reference must be replaced by actual vector cases.
Disposition contradictory documentarycount43vs41 must be consistent.

Review acceptance remains user-owned; above is not an approval of later revisions.

## Scoped final review of NEW-1/I5/I3/I4 — technical APPROVE

Actual standalone CLI session94bf9517-bdda-4dbb-b4b2-20b27f0fd115 returned
APPROVE (technical review only), modelUsage claude-opus-5-5, firstParty, no
subagents. All50 then-current scope hashes verified. State33+crypto28 tests
passed61/61; independent oracle mutation probes rejected honest forced-peerpoint
substitution and off-curve corruption. NEW-1, I5, I3 and I4 each ADDRESSED:
client-reported0x02 gates recoveryprepare; active clientmirror durable across
restart; committed forcedpointbytes used by oracle; curated testrefs only.
No load-bearing regression in that scoped fix. Minor unusedREQ_ID_PATTERN and
superseded old disposition text noted. Approval does not cover later R1
subagent findings/corrections or imply user acceptance; final revalidation of
latest content remains pending.

## Scheduled continuation: R1/R2/R3 and persistence revalidation — APPROVE

Actual standalone session `94bf9517-bdda-4dbb-b4b2-20b27f0fd115` was resumed
twice with `--print --output-format json --model opus --permission-mode plan`.
Both CLI results returned `is_error: false`, `terminal_reason: completed`,
`modelUsage.claude-opus-5-5` with canonical model `claude-opus-5-5`,
`provider: firstParty`, and **0 subagents**. These runtime fields are separate
from the reviewer's own self-identification in its written response.

1. Timeout/record-conditional R1 correction plus R2/R3: actual verdict
   **APPROVE (technical review only)**; all50 hashes matched; state34/34.
   R1, R2, R3 each ADDRESSED. This verdict preceded the persistence correction.
2. Final ET/atomic-persistence-before-release correction: actual verdict
   **APPROVE (technical review only)**; all50 refreshed hashes matched;
   independently ran state35/35. Reviewer checked ET has no circular
   dependency: the own canonical body is locally known and the peer body
   authenticated before deriving ET; both roles persist complete pending
   records before release. Failure gates release nothing and leave at most
   a one-sided inactive record, handled by the already defined cleanup path.
   Prepare, one-use-code, and durable active-client-mirror fixes retain their
   earlier approvals. The reviewer did not reproduce the broad aggregate.

Final response result UUID: `9c4422a0-f340-4022-9a31-2ab976ef8c99`.
Final reviewed hashes are the 50-entry block in `otp-02-brief.md`, with key
digests in the final disposition. No approval is inferred for future edits.

### Nonblocking notes retained for user disposition

- The disconnect-before-identity fixture could independently require the
  repair-only and old-window-rejected effects; existing step assertions and
  structural pins cover these today.
- The client durable `absent → pending` row lacks the duplicate-installation
  guard present on `deleted → pending`; the enforcing guard is phone-side.
- Durable machines have explicit `absent` persistence-failure cleanup but no
  explicit `deleted + persist_ok:false → deleted` row for failed re-enrollment.
- Phone persistence failure names connection close; key erasure follows the
  normative connection-end rule, but could be named explicitly as a test effect.
- Reviewer judged record-conditional recovery a necessary clarification; user
  decides whether an additional explicit RFC note is desirable.

No blocking scoped findings remain. User acceptance and Gate D external review
remain separate from these technical approvals.
