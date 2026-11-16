## Design Summary

The review gate re-reviews too often. Its always-on rule ends with **Any edit reopens review**, and the procedure's decision table confirms every cosmetic edit with a reviewer round and opens a new pass for every normative edit. Agents therefore call Sol or Opus again after a typo fix or a two-line wording change.

The change replaces that open-ended trigger with a bounded one:

- A fix of a **critical or high** finding, or a **rebuttal** of one, still requires a reviewer round. Critical and high findings must still be closed by the reviewer before an artifact is approved.
- Fixing **more than five minor findings** (medium, low or cosmetic) since the last completed review also requires re-review. The count is per artifact and cumulative; only a new completed review resets it.
- Fixing **five or fewer minor findings**, or making a cosmetic edit, does not reopen review. The agent records the dispositions and re-points the affected `Approved content` hashes with a note naming the fixes.
- A normative edit that is not a finding fix still opens a new pass. The threshold applies to finding fixes, not to silently rewriting an approved artifact.
- Upstream edits follow the same threshold: a non-triggering upstream edit only re-points the downstream records with a note; a triggering one makes the downstream approvals stale and needs the existing consistency revalidation.
- During an active pass, a triggering fix continues the same reviewer's counter; a new pass opens only after the cycle is complete. With a two-reviewer preset, the first reviewer's confirmation pass is the only extra pass inside a cycle, and it is needed only when the second reviewer's accepted fixes are triggering. Non-triggering fixes are covered by the second reviewer's approving round and the first reviewer's record is re-pointed.

Everything else stays: the first review of every planning file, the ask-first preset, the 5-round cap per reviewer per pass, escalation at a cap, the records and audit, and the stop before apply. No new files, logs or mechanisms are introduced; the existing rule, procedure, spec and schema text is rephrased in place.

## Alternatives Considered

### Alternative A: Severity and count threshold with hash re-pointing (chosen)

- **Approach**: Define one trigger in the procedure doc, repeat it in the always-on rule, and rewrite the decision table, revision binding and audit around it. A non-triggering edit is recorded and its hash re-pointed with a note, so the existing content-bound audit still works without a reviewer round.
- **Pros**: Directly removes the re-review churn; keeps critical and high findings under reviewer control; keeps the records meaningful and auditable; no new artifact or process.
- **Cons**: The audit cannot prove that a re-pointed edit was really minor; enforcement remains instruction-level, as it is today.
- **Why chosen**: It is the smallest change that satisfies the request and preserves every existing safety property.

### Alternative B: Count minor fixes per batch

- **Approach**: Only the minor fixes made in one response to one review are counted; each batch of five or fewer is free.
- **Pros**: Even less re-review churn; simple to apply in a single round.
- **Cons**: An artifact can drift through any number of five-fix batches without ever being re-reviewed, defeating the purpose of the threshold.
- **Why not chosen**: The user chose the cumulative count to bound slow drift.

### Alternative C: Change only the procedure doc

- **Approach**: Rephrase `docs/superspec-workflow.md` and leave the always-on rule, spec and schema untouched.
- **Pros**: Smallest diff.
- **Cons**: The always-on rule still says **Any edit reopens review**, so an agent reading the rule alone would keep re-reviewing; the spec and verify audit would contradict the procedure.
- **Why not chosen**: The rule is the contract an agent reads first; all copies must agree.

### Alternative D: Literal only-finding triggers

- **Approach**: Only critical/high fixes and more than five minor fixes ever reopen review; a normative rewrite that is not a finding fix is recorded and re-pointed without review.
- **Pros**: Maximally literal and quiet.
- **Cons**: An approved artifact could be rewritten normatively with no review at all, which removes the quality guard the current **Normative edit after the cycle** row provides.
- **Why not chosen**: The user chose to keep the normative-edit guard.

## Agreed Approach

Use Alternative A. The procedure doc owns the definition and the decision table; the always-on rule states the trigger in one short line and points to the procedure; the spec delta makes the trigger normative; the schema verify check, its template and the apply pre-flight's absolute "never rewrite" sentence all use the re-point note condition. The wording is shortened, not expanded: the existing rows for cosmetic confirmation and normative edits are replaced by the one threshold, and the existing upstream, confirmation and audit machinery is rephrased to use it.

## Key Decisions

- **Minor finding**: medium, low or cosmetic. **Cosmetic** means a wording or formatting change that does not change meaning.
- **Cumulative count**: minor fixes count per artifact since the last completed review (every reviewer the preset lists approved); the sixth triggers re-review; only a new completed review resets the count, not an intermediate round or one reviewer's approval.
- **Critical/high**: any fix or rebuttal of a critical or high finding requires a reviewer round.
- **Non-triggering edits**: up to five minor fixes, a cosmetic edit, or a minor rebuttal or deferral. Record the disposition and re-point the affected `Approved content` hash with a note; no reviewer round.
- **Triggering edits**: a critical or high fix or rebuttal, more than five minor fixes since the last completed review, or a normative edit that is not a finding fix. During an active pass the same reviewer continues the same counter; after the cycle every approving reviewer re-reviews in preset order as a new pass.
- **Upstream edits**: same threshold. Non-triggering: re-point the downstream records with a note. Triggering: downstream approvals are stale and each approving reviewer runs the consistency revalidation, capped at 2 rounds each.
- **Two-reviewer preset**: the first reviewer's confirmation pass runs only for triggering fixes by the second reviewer; non-triggering fixes are covered by the second reviewer's approving round and the first record is re-pointed. At most one confirmation pass per cycle; a demand for a critical or high fix escalates.
- **Schema alignment**: the verify check, its template and the apply pre-flight's absolute "never rewrite a recorded hash" sentence all use the same rule: a re-pointed hash must carry its note; a triggering edit that was not re-reviewed refuses apply.
- **Unchanged**: every planning file is reviewed before use; the ask-first preset; 5 rounds per reviewer per pass; escalation; the records layout and hash command; the verify audit; the stop before apply.

## Open Questions

None. The cumulative count and the retained normative-edit guard were confirmed by the user.
