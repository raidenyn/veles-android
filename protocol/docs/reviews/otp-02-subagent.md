# Independent reviewer subagent — first pass

Session: ses_e9302e4b8ffeiKxQXgOWGVUboK. Fresh reviewer role, no authorship,
read-only. Verdict REQUEST_CHANGES. All50 scope hashes matched; targeted state
suite33/33 passed. This is not approval of later revisions.

## Critical R1: cB-to-identity interruption cannot always recover

`veles-v1.md:198-208,1908-1919` claims all post-cB disconnects exclusively
resume via signed recovery. Phone consumes code at states.json419-436 but only
persists peer record after client enroll_identity (2660-2674). Disconnect in
enrollment_protected returns idle828-835 without peer public key. Client similarly
persists only after phone identity1001-1015. Existing one-use fixture disconnects
after identity persistence, missing this boundary.
Fix exact behavior for interruption immediately aftercB/before any peer identity:
old code remains consumed; endpoints without usable peer record require a fresh
explicit pairing window/new pairingID/new code, not imaginary signed recovery.
Preserve bounded recovery for endpoints with actual matching pending records;
cover partial one-sided record cleanup/re-pair path.

## Critical R2: mismatched event_ack contradictory

Normative1348-1355 unknown-event authenticated ack accepted semantic no-op and
consumes sequence, but1365-1373 calls mismatched ack rejected while global70-74
says rejection tears session down. Descriptor1762-1781 and records fixture1040-1145
choose accepted no-op. Make normative sentence agree: authenticated well-formed
unknown-event ack is consumed semantic no-op, no deliverycredit; malformed/badtag/
wrongsequence still fatal. No ambiguous rejection vocabulary.

## Minor R3: six-attempt typo

State-table note1911-1914 says six-attempt perID but fixed limit5. Correct to
five-attempt limit/sixth refused.

User acceptance and final release external review remain separate.

## Scheduled continuation: scoped persistence re-review — APPROVE

The original reviewer's CLI resume timed out without a verdict. A fresh
independent reviewer was dispatched as authorized: session
`ses_e917e3343ffelct6W44guU6hZA`, reviewer role (configured
`openai/gpt-5.6-sol` routing; the task response does not independently attest
the runtime model). Read-only; no authorship of this contract.

Its first scoped review returned REQUEST_CHANGES: R2 and R3 ADDRESSED, but
R1 NOT ADDRESSED because the client released its identity before computing
and durably storing required `ET`. The phone also persisted before computing
`ET`. The reviewer additionally found the unconditional descriptor summary.
The controller verified the finding and resumed the GLM implementation worker.

The same fresh reviewer then returned **APPROVE** after the correction:

- R1 usable-record persistence ADDRESSED: normative ET-first, atomic complete
  pending persistence, then release (`veles-v1.md:706-718,785-795`); both
  state tables aligned (`:1956,1998`). Phone/client success and failure rows
  and durable-record gates match; failure releases nothing.
- Stale unconditional summary ADDRESSED (`states.json:5`).
- Negative persistence-failure and successful-persistence/interruption
  fixtures cover the boundary (`enrollment.json:2854-3115`), with structural
  pins and required-ID guards (`state.test.mjs:348-392,1066-1071`).
- No blocking fix regressions observed in the authorized scope.
- Independently verified all **50/50** current brief hashes (0 mismatches),
  state suite **35/35**, and `check-contract-text` PASS (156 requirements,
  61 limits). The reviewer did not rerun the broad Gradle aggregate.

Reviewed content is the 50-entry hash block in `otp-02-brief.md`; key final
digests are recorded in the final disposition below. R2/R3 approval from the
first scoped pass remains applicable; their fixes were untouched by the
persistence correction. This is technical review, not user acceptance.
