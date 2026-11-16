## Context

The gate's current rule ends with **Any edit reopens review**, and its procedure confirms each cosmetic edit with a reviewer round and opens a new pass for every normative edit. Approvals are bound to sha256 hashes, and the verify audit and apply pre-flight refuse a mismatch, so any policy that skips a re-review must also define how the recorded hash stays current.

The change is documentation only. It edits the always-on rule, the procedure doc, the `superspec-review-gate` spec delta, the superspec schema's verify check and apply pre-flight, and the verify template. The roster, the ask-first step, the 5-round cap, the record layout and hash command, the apply stop, `openspec/config.yaml`, generated skills and implementation review are unchanged. The always-on rule must stay within its 4,096-byte cap, so it carries only the trigger and points to the procedure for the detail.

## Goals / Non-Goals

**Goals:**
- Re-review only after a critical or high fix or rebuttal, or more than five minor fixes cumulatively since the last completed review.
- Record and hash-re-point up to five minor fixes, cosmetic edits, and minor rebuttals or deferrals without a reviewer round.
- Keep critical and high findings under reviewer control, the pass cap, the two-reviewer cycle bound, the audit and the apply stop.
- Rephrase the existing text in place; add no file, log, command or mechanism, and keep every rule short.

**Non-Goals:**
- No change to the roster or presets, the ask-first step, implementation review, the tracker or closeout, `openspec/config.yaml`, or the generated skills.
- No automated enforcement beyond the existing audit; the classification remains instruction-level.

## Decisions

### D1. Trigger definition

- **Minor** means medium, low or cosmetic. **Cosmetic** is a wording or formatting change that does not change meaning.
- **Triggering**: a critical or high fix, a critical or high rebuttal that must be judged, more than five minor fixes cumulatively since the last completed review, or a normative edit that is not a finding fix.
- **Non-triggering**: up to five minor fixes, a cosmetic edit, or a minor rebuttal or deferral.
- **Count baseline**: per artifact, since the last completed review — every reviewer the preset lists approved. Only a new completed review resets the count; an intermediate round or one reviewer's approval does not.

### D2. Active pass versus completed cycle

During an active pass, a triggering fix continues the same reviewer's counter; it does not open a new pass. After the cycle is complete, a triggering edit opens a new pass for every approving reviewer in preset order; a non-triggering edit is recorded and its hash re-pointed with a note.

### D3. Two-reviewer confirmation

The first reviewer approves before the second starts. If the second reviewer's accepted fixes are triggering, the first reviewer runs exactly one confirmation pass, capped at 2 rounds; a demand for a critical or high fix escalates instead of reopening the second reviewer. Non-triggering fixes need no confirmation: the second reviewer's approving round covers them and the first reviewer's record is re-pointed with a note. At most one confirmation pass runs per cycle.

### D4. Upstream edits

The same threshold applies. A non-triggering upstream edit only re-points the affected upstream hashes in the downstream records' headers with a note and opens no pass. A triggering upstream edit makes the downstream approvals stale and needs the existing consistency revalidation by each approving reviewer, capped at 2 rounds each; the author alone must not revalidate it.

### D5. Hash re-pointing and audit

The allowed hash rewrites are a re-point after a non-triggering edit: the artifact's `Approved content` line, and any affected upstream hash in the record header. Each changed hash carries a note naming the fixes it covers; the `Approved content` note follows the hash, the header note follows the hash it changes. An untouched hash needs no note, so existing records stay valid. The audit checks the note on every changed hash; a changed hash without it is FAIL. The procedure's absolute "never recompute or rewrite a recorded hash" sentence becomes "never rewrite a hash to hide a triggering edit." The apply pre-flight's "a recorded hash is never rewritten" sentence becomes the same rule, and the verify check and its template use the note condition.

### D6. Exact edit locations

- `docs/superspec-workflow.md`:
  - *Grading and the verdict*: keep the critical/high closure sentence; point to *Review passes and caps* for the threshold.
  - *Review passes and caps*: in *What a pass is*, add the D1 definitions and the D2 active/completed distinction; replace the cosmetic and normative rows in the decision table with triggering/non-triggering rows; rewrite *Confirmation and revalidation* with D3 and D4.
  - *Revision binding*: in *The body and the approval line*, describe the D5 re-point and note; in the audit checklist steps 2 and 3, require the note on a changed line; replace the "never recompute or rewrite" paragraph with the D5 rule.
  - *Escalation*: change "a confirmation pass demands normative edits" to "a confirmation pass demands a critical or high fix."
- `.agents/rules/repo-superspec-review-gate.md`: replace **Any edit reopens review.** with one two-sentence line carrying D1's trigger and the non-triggering record/re-point rule.
- `openspec/changes/superspec-re-review-threshold/specs/superspec-review-gate/spec.md`:
  - MODIFIED: *A review pass is bounded*, *Two reviewers run in order with a bounded confirmation*, *Review records bind approvals to content*, *Escalation at a cap explains the problem to a human*, *The review-records audit runs whenever verification runs*, *The workflow stops before apply*.
  - In *Review records bind approvals to content*, qualify "Editing a task's text": an edit without an allowed noted re-point makes the approval stale; a non-triggering fix with a note keeps it approved.
  - In *The workflow stops before apply*, narrow the "Stale plan" and "Stale upstream" scenarios: a triggering edit is stale and refuses apply; a non-triggering edit with a noted re-point is current and does not.
  - RENAMED: *Edits and upstream changes reopen review* to *Only significant fixes reopen review*, with its body and scenarios rewritten.
- `openspec/schemas/superspec/schema.yaml`: verify check 6 adds the note condition; apply step 0a replaces the absolute hash sentence with the D5 rule.
- `openspec/schemas/superspec/templates/verify.md`: section 6 rephrases the existing "never rewrite a recorded hash" sentence to allow a noted non-triggering re-point and adds one checklist line for it.
- `docs/AGENTS.md`: the divergence list's apply pre-flight and sixth-verify-check items mention the note condition so a schema reinstall re-applies it.

### D7. Wording constraints

No new heading or table is added beyond the rephrased *What a pass is* and the existing decision table. The rule keeps its existing bullets and stays under 4,096 bytes. The procedure doc uses one short definitions paragraph and a 10-row table; every other section keeps its current shape. The spec delta reuses the existing requirement names except the one rename.

## Risks / Trade-offs

- **Misclassification** — an agent claims a triggering edit is minor and re-points the hash. → The note must name each fix and its finding; the audit checks the note; a reviewer round or the user settles a dispute. The trade-off is accepted; enforcement remains instruction-level.
- **Untracked drift** — an agent fails to record a minor fix, so the cumulative count is wrong. → Every disposition is recorded in the pass table; the reviewer sees the record at the next review and the audit can inspect it.
- **False audit failure** — a re-pointed hash lacks the expected note. → The procedure, the verify check and its template all state the note requirement and the exact line shape.
- **Two-reviewer ambiguity** — the first reviewer's record is re-pointed after the second reviewer's non-triggering fixes. → The decision table and D3 state that the second reviewer's approving round covers the fixes and that the first record is re-pointed with a note.
- **Backward compatibility** — old approvals have no note. → Only a changed `Approved content` line needs a note; an untouched approval stays valid.

## Migration Plan

Edit the files in one commit. Run `python3 docs/checks/check_agent_files.py --root .`, the docs check unit tests and `openspec validate --all`; grep for **Any edit reopens review** and the cosmetic-confirmation wording to prove no copy is left. No runtime deploy or data migration. The new rule applies to reviews after the commit; an active change's existing records are untouched until an edit re-points them. Rollback is a revert of the commit.

## Open Questions

None.
