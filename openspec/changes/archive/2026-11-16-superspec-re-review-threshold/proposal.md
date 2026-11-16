## Why

The review gate re-reviews after every edit. The always-on rule says **Any edit reopens review**, and the procedure confirms each cosmetic edit with a reviewer round and opens a new pass for every normative edit. Agents therefore call Sol or Opus again after a typo fix or a two-line wording change, which is slow and expensive. The gate should re-review significant fixes only, while still keeping critical and high findings under reviewer control.

## What Changes

**Re-review trigger**
- From: any edit after approval reopens review; a claimed cosmetic edit needs one confirming reviewer round; a normative edit opens a new pass for every approving reviewer.
- To: re-review only when a fix addresses a critical or high finding, or a critical or high rebuttal is judged, or when more than five minor findings (medium, low or cosmetic) are fixed cumulatively since the last completed review. Five or fewer minor fixes, cosmetic edits, and minor rebuttals or deferrals are recorded and their approval hashes re-pointed with a note; no reviewer round.
- Reason: removes the re-review churn the current rule causes.
- Impact: non-breaking policy change for the gate's planning and closeout documentation reviews; implementation review is untouched.

**Count baseline**
- From: no count; the question is whether an edit is normative or cosmetic.
- To: the count is per artifact since the last completed review (every reviewer the preset lists approved). Only a new completed review resets it.
- Reason: a two-reviewer preset needs one baseline; an intermediate round or one reviewer's approval must not reset it.

**Active pass and two-reviewer confirmation**
- From: every accepted edit during a two-reviewer cycle can open a confirmation pass or be confirmed as cosmetic.
- To: during an active pass a triggering fix continues the same reviewer's counter. The first reviewer's confirmation pass runs only when the second reviewer's accepted fixes are triggering; non-triggering fixes are covered by the second reviewer's approval and the first record is re-pointed. At most one confirmation pass per cycle; a demand for a critical or high fix escalates.
- Reason: keeps the 5-round cap and the one-confirmation-per-cycle bound intact. The escalation procedure and its six parts are unchanged; its confirmation-pass trigger becomes a critical or high fix instead of any normative edit.

**Upstream edits**
- From: any upstream edit makes downstream approvals stale and needs a consistency revalidation.
- To: the same threshold applies. A non-triggering upstream edit only re-points the downstream records with a note; a triggering one makes the approvals stale and needs the consistency revalidation.
- Reason: an upstream typo fix must not fan out into re-reviews.

**Normative edit that is not a finding fix**
- From: opens a new pass.
- To: unchanged. The threshold applies to finding fixes, not to silently rewriting an approved artifact.

**Records, audit and schema text**
- From: a recorded hash is never rewritten; the audit fails any mismatch; the apply pre-flight refuses a stale hash.
- To: a hash may be re-pointed only for a non-triggering edit, with a note naming the fixes. The audit and apply pre-flight require that note and still fail a triggering edit that was not re-reviewed. All other records, hash computation, caps and the apply stop are unchanged.
- Reason: the content-bound audit must keep working without a reviewer round.

**Concise wording**
- The always-on rule, procedure doc, spec and schema text are rephrased in place. No new file, log, command or mechanism.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `superspec-review-gate`: the re-review trigger changes from "any edit" to a critical/high or more-than-five-minor threshold with a cumulative per-artifact count; non-triggering fixes are recorded and hashes re-pointed with a note; upstream edits and the two-reviewer confirmation follow the same threshold; the audit and apply pre-flight accept a noted re-point and still fail an unreviewed triggering edit.

## Impact

- Edited files: `docs/superspec-workflow.md` (*Grading and the verdict*, *Review passes and caps*, *Revision binding*, *Escalation*), `.agents/rules/repo-superspec-review-gate.md`, the `superspec-review-gate` delta spec, `openspec/schemas/superspec/schema.yaml` (verify check 6 and apply step 0a) and `openspec/schemas/superspec/templates/verify.md`.
- Unchanged: the roster, the ask-first step, the 5-round cap, the escalation procedure and its six parts, the record layout and hash command, the apply stop, `openspec/config.yaml` hooks, generated skills and all implementation review.
- Checks: `python3 docs/checks/check_agent_files.py --root .`, `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v`, `openspec validate --all`, and a grep that no copy still says **Any edit reopens review** or requires a cosmetic confirmation round. The always-on rule stays within its 4,096-byte cap.
- Risk: an agent can misclassify a triggering edit as minor; the required disposition note and the audit make that visible but do not prove it. This is the trade-off the user accepted to remove the churn.
