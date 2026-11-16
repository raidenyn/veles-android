# Superspec Re-review Threshold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the superspec gate's "any edit reopens review" trigger with a critical/high or more-than-five-minor threshold, and let non-triggering fixes re-point their approval hashes with a note instead of a reviewer round.

**Architecture:** Documentation-only, in-place edits. The procedure doc owns the threshold and the decision table; the always-on rule carries one short line and points there; the spec delta is the acceptance contract; the schema verify check and apply pre-flight accept a noted re-point and still fail an unreviewed triggering edit. No new file, log, command or mechanism.

**Tech Stack:** Markdown, the superspec `schema.yaml`, the verify template, and the repository's stdlib Python checks.

**Spec:** `openspec/changes/superspec-re-review-threshold/specs/superspec-review-gate/spec.md` (delta) and `design.md`.

## Global Constraints

- **Trigger:** only a critical or high fix or rebuttal, more than five minor findings (medium, low, cosmetic) fixed since the last completed review, or a normative edit that is not a finding fix reopens review.
- **Non-triggering:** up to five minor fixes, a cosmetic edit (wording or formatting with no meaning change), or a minor rebuttal or deferral. Record it and re-point the affected hash with a note immediately after the hash.
- **Count:** per artifact, since the last completed review — every reviewer the preset lists approved. An intermediate round or one reviewer's approval does not reset it. During an active pass, a triggering fix continues the same reviewer's counter.
- **Preserve:** the first review of every planning file, the ask-first preset, the 5-round cap, one confirmation pass per two-reviewer cycle, the escalation six parts, the record layout and hash command, the apply stop, and implementation review.
- **Rule size:** `.agents/rules/repo-superspec-review-gate.md` stays at or below 4,096 bytes.
- **No version bump:** `schema.yaml` stays at `version: 5`.
- **Main spec:** `openspec/specs/superspec-review-gate/spec.md` keeps the old text until the closeout sync; do not edit it here.
- **Untouched skills:** no file under `.agents/skills/` or `.claude/skills/` changes, and no model name enters a workflow file.
- **One commit:** the design's migration calls for one commit. Tasks 1–5 stage their edits; Task 6 makes the single commit after all checks pass, so no intermediate commit leaves the policy copies inconsistent.

## Review Focus

- A user-requested normative edit is not a finding fix and must open a new pass, or approved content could be rewritten unreviewed.
- A sixth minor fix after an earlier batch of five must trigger review, or the cumulative count is meaningless.
- A noted re-point must not be accepted without its note, and the note must not be used to hide a triggering edit.
- The two-reviewer confirmation must run at most once per cycle and must not reopen the second reviewer.
- An untouched approval needs no note; otherwise every existing record would fail retroactively.

---

### Task 1: Procedure doc — threshold, decision table and confirmation rules

**Files:**
- Modify: `docs/superspec-workflow.md` (*Reviewer brief → Grading and the verdict*, *Review passes and caps*)
- Test: content checks and a read-back below (no test file for docs)

**Interfaces:**
- Consumes: the approved threshold and count in `specs/superspec-review-gate/spec.md`, requirements *A review pass is bounded*, *Two reviewers run in order with a bounded confirmation* and *Only significant fixes reopen review*.
- Produces: the definitions, the 10-row decision table and the confirmation/revalidation rules that Tasks 2, 3 and 4 cite.

- [ ] **Step 1: Record the starting revision**

Run `git rev-parse HEAD` and keep the value as `<start>` for Task 6.

- [ ] **Step 2: Confirm the old trigger is present (red check)**

```bash
grep -n "claimed cosmetic\|Normative edit after the cycle\|the second reviewer's accepted edits change normative content" docs/superspec-workflow.md
```

Expected: matches in the decision table and *Confirmation and revalidation*. Do not include `demands normative edits` here; the *Escalation* section still owns it until Task 2.

- [ ] **Step 3: Rewrite *Grading and the verdict***

Keep the critical/high closure sentence and the medium/low disposition sentence. Add one sentence: a fix of up to five minor findings or a cosmetic edit does not reopen review; the threshold and the count are in *Review passes and caps*. Do not add a heading.

- [ ] **Step 4: Rewrite *Review passes and caps → What a pass is***

Keep the pass and round definitions. Change "every edit" to "every re-review". Replace the blanket rebuttal sentence with: a critical or high rebuttal gives a reason, is judged in the next round and consumes a round; a minor rebuttal or deferral is recorded and does not open a round. Add one paragraph defining **minor**, **cosmetic**, **triggering**, **non-triggering**, the per-artifact count since the last completed review, and active-pass counter continuity. Use the exact definitions in the Global Constraints.

- [ ] **Step 5: Replace the decision table***

Use these rows, in this order:

| Event | Reviewed by | Cap | Recorded |
|---|---|---|---|
| A planning file is first written | each preset reviewer, in order | 5 rounds each | a pass section and an approval line with the hash |
| A triggering fix during a pass | the same reviewer | same counter, no reset | rounds in the pass |
| A non-triggering fix during a pass | none | none | dispositions; hash re-pointed with a note |
| The second reviewer's accepted fixes are triggering | first reviewer, one confirmation pass | 2; escalate on a critical or high fix | confirmation pass |
| The second reviewer's accepted fixes are not triggering | none | none extra | the second reviewer's approval covers them; first record re-pointed with a note |
| A triggering edit after the cycle | every approving reviewer, in order, new pass | 5 each | new pass |
| A non-triggering edit after the cycle | none | none | hash re-pointed with a note |
| A triggering upstream edit | each approving reviewer, in order, consistency check | 2 each | new upstream hashes |
| A non-triggering upstream edit | none | none | downstream records re-pointed with a note |
| Any cap reached | stop; the escalation template goes to the user | none | the escalation |

- [ ] **Step 6: Rewrite *Confirmation and revalidation***

State: triggering fixes from the second reviewer get exactly one confirmation pass, capped at 2 rounds; a demand for a critical or high fix escalates instead of reopening the second reviewer; non-triggering fixes get no pass and the first record is re-pointed. A consistency revalidation after a triggering upstream edit is its own pass, capped at 2 rounds per reviewer; a non-triggering upstream edit only re-points. A non-triggering post-cycle edit re-points its hashes; a triggering post-cycle edit opens a new pass for every approving reviewer. An edit during an active two-reviewer cycle follows the confirmation rules and nothing else. This task changes only the confirmation text; the *Escalation* section itself belongs to Task 2.

- [ ] **Step 7: Run the content checks (green)**

```bash
grep -n "claimed cosmetic\|Normative edit after the cycle\|the second reviewer's accepted edits change normative content" docs/superspec-workflow.md
grep -n "more than five minor\|last completed review\|normative edit that is not a finding fix\|non-triggering\|one confirmation pass\|reopening the second reviewer" docs/superspec-workflow.md
```

Expected: the first command prints nothing; the second prints the new definitions, table rows and confirmation text.

- [ ] **Step 8: Read-back acceptance for the Review Focus cases**

Read *Grading and the verdict*, *What a pass is*, the decision table and *Confirmation and revalidation* once, and confirm:

- a normative edit that is not a finding fix opens a new pass;
- five minor fixes after the last completed review need no round, while the sixth opens one, and neither an intermediate round nor one reviewer's approval resets the count;
- at most one confirmation pass runs per two-reviewer cycle and the second reviewer is not reopened;
- every non-triggering row names the note and the hash re-point.

- [ ] **Step 9: Stage the procedure edit**

```bash
git add docs/superspec-workflow.md
```

Do not commit; Task 6 makes the single commit.

---

### Task 2: Procedure doc — records, upstream qualification, audit and escalation

**Files:**
- Modify: `docs/superspec-workflow.md` (*Revision binding → The body and the approval line*, *Upstream and hashes*, *The audit checklist*, *Escalation*)
- Test: content checks and a read-back below

**Interfaces:**
- Consumes: Task 1's threshold and the spec requirements *Review records bind approvals to content*, *The review-records audit runs whenever verification runs* and *Escalation at a cap explains the problem to a human*.
- Produces: the re-point note rule, the upstream qualification and the audit failure conditions that Task 4 mirrors in the schema and template.

- [ ] **Step 1: Confirm the old absolute rules are present (red check)**

```bash
grep -n "Never recompute or rewrite a recorded hash\|demands normative edits\|a design edit also makes later approvals stale" docs/superspec-workflow.md
```

Expected: all three phrases found.

- [ ] **Step 2: Add the re-point rule to *The body and the approval line***

After the approval-line paragraph, add: a record may re-point its `Approved content` line or an upstream hash in its header only after a non-triggering edit; the note follows the changed hash and names the fixes; an untouched hash needs no note.

- [ ] **Step 3: Update the audit checklist steps 2 and 3***

Require the note on every changed hash. A changed hash without the note is FAIL. A triggering edit that was not re-reviewed is FAIL. The upstream hash check has the same note condition.

- [ ] **Step 4: Replace the "never rewrite" paragraph***

Replace "Never recompute or rewrite a recorded hash to make an audit pass..." with: never rewrite a hash to hide a triggering edit; a noted non-triggering re-point is the one allowed rewrite; a rewrite without the note is treated as no approval. Keep the "only a reviewer round or the user settles it" sentence.

- [ ] **Step 5: Qualify *Upstream and hashes***

Replace "a design edit also makes later approvals stale" with "a triggering design edit also makes later approvals stale; a non-triggering one only re-points the downstream records with a note." Keep the "stricter than the schema's dependency graph" explanation and the audit's full-upstream-set rule.

- [ ] **Step 6: Change the *Escalation* trigger***

Replace "demands normative edits" with "demands a critical or high fix" in the *Escalation* section. Task 1 already changed the sentence in *Confirmation and revalidation*; do not edit that sentence again. Keep the six parts and the wait rule.

- [ ] **Step 7: Run the content checks (green)**

```bash
grep -n "Never recompute or rewrite a recorded hash\|demands normative edits\|a design edit also makes later approvals stale" docs/superspec-workflow.md
grep -n "re-point.*note\|never rewrite a hash to hide\|untouched hash needs no note\|triggering design edit\|demands a critical or high fix" docs/superspec-workflow.md
```

Expected: the first command prints nothing; the second prints the new rule, the upstream qualification and the changed escalation trigger.

- [ ] **Step 8: Read-back acceptance for the Review Focus cases**

Read *The body and the approval line*, *Upstream and hashes* and *The audit checklist* once, and confirm:

- a changed hash without its note fails the audit, and a triggering edit that was not re-reviewed fails even with a note;
- an untouched hash needs no note, so existing records stay valid;
- a non-triggering upstream edit only re-points the downstream records, while a triggering one still makes them stale.

- [ ] **Step 9: Stage the procedure edit**

```bash
git add docs/superspec-workflow.md
```

Do not commit; Task 6 makes the single commit.

---

### Task 3: Always-on rule

**Files:**
- Modify: `.agents/rules/repo-superspec-review-gate.md`
- Test: `python3 docs/checks/check_agent_files.py --root .` and `wc -c`

**Interfaces:**
- Consumes: Task 1's definitions and the existing intro pointer to *Review passes and caps*.
- Produces: the short rule an agent reads alone; it must still state the threshold and the non-triggering re-point.

- [ ] **Step 1: Confirm the old bullet is present (red check)**

```bash
grep -n "Any edit.*reopens review" .agents/rules/repo-superspec-review-gate.md
```

Expected: the bold bullet is found.

- [ ] **Step 2: Replace the bullet***

Replace `- **Any edit** reopens review.` with:

```markdown
- **Re-review:** only a critical or high fix or rebuttal, more than 5 minor findings (medium, low, cosmetic) fixed since the last completed review, or a normative edit that is not a finding fix, reopens review. Up to 5 minor fixes and cosmetic edits are recorded and the approval hash re-pointed with a note.
```

Keep the intro sentence, the other bullets and the file at or below 4,096 bytes.

- [ ] **Step 3: Run the content and size checks (green)**

```bash
grep -n "Any edit.*reopens review" .agents/rules/repo-superspec-review-gate.md
grep -n "more than 5 minor\|normative edit that is not a finding fix\|re-pointed with a note" .agents/rules/repo-superspec-review-gate.md
wc -c .agents/rules/repo-superspec-review-gate.md
python3 docs/checks/check_agent_files.py --root .
```

Expected: the first command prints nothing; the second prints the new line; the size is at most 4096; the check exits 0 (warnings allowed).

- [ ] **Step 4: Stage the rule edit**

```bash
git add .agents/rules/repo-superspec-review-gate.md
```

---

### Task 4: Schema verify check and apply pre-flight

**Files:**
- Modify: `openspec/schemas/superspec/schema.yaml` (verify check 6, apply step 0a(b))
- Test: content checks and `openspec validate --all`

**Interfaces:**
- Consumes: Task 2's audit note rule and the spec requirement *The workflow stops before apply*.
- Produces: the schema text that the verify skill and apply pre-flight read.

- [ ] **Step 1: Confirm the old absolute sentence is present (red check)**

```bash
grep -n "recorded hash is never" openspec/schemas/superspec/schema.yaml
```

Expected: found in apply step 0a(b).

- [ ] **Step 2: Update verify check 6***

In the `verify` instruction's sixth check, after the list of FAIL conditions, add: a hash re-pointed after a non-triggering edit must carry its note; a re-point without that note, or a triggering edit that was not re-reviewed, is FAIL. Keep the six-check count and the reference to *Revision binding*.

- [ ] **Step 3: Update apply step 0a(b)***

Replace "a recorded hash is never rewritten to make it match" with: a hash re-pointed for a non-triggering edit must carry its note; a triggering edit that was not re-reviewed refuses the start. Keep the plan's own hash, upstream set and coding-model conditions.

- [ ] **Step 4: Run the content checks (green)**

```bash
grep -n "recorded hash is never" openspec/schemas/superspec/schema.yaml
grep -n "non-triggering edit must carry its note\|triggering edit that was not re-reviewed" openspec/schemas/superspec/schema.yaml
openspec validate --all
```

Expected: the first command prints nothing; the second prints both the verify and apply text; validation passes.

- [ ] **Step 5: Stage the schema edit**

```bash
git add openspec/schemas/superspec/schema.yaml
```

---

### Task 5: Verify template and schema divergence notes

**Files:**
- Modify: `openspec/schemas/superspec/templates/verify.md` (section 6)
- Modify: `docs/AGENTS.md` (divergence items 1 and 2)
- Test: content checks below

**Interfaces:**
- Consumes: Task 4's schema text and the spec requirement *The schema fork carries the gate and records its divergences*.
- Produces: the template checklist item and the reinstall instructions.

- [ ] **Step 1: Confirm the old template prohibition is present (red check)**

```bash
grep -n "never rewrite a recorded hash" openspec/schemas/superspec/templates/verify.md
```

Expected: found in section 6.

- [ ] **Step 2: Rewrite the template section 6 sentence and add the checklist line***

Replace "never rewrite a recorded hash" with "a changed hash must carry its note; never rewrite a hash to hide a triggering edit". Add `- [ ] Every changed hash carries the note for its non-triggering edit` to the checklist. Keep the table columns and the FAIL rule.

- [ ] **Step 3: Extend the divergence notes***

In `docs/AGENTS.md` item 1 (apply pre-flight) and item 2 (sixth verify check), mention the note condition so a schema reinstall re-applies it. Do not add a version-history entry; the version stays 5.

- [ ] **Step 4: Run the content checks (green)**

```bash
grep -n "never rewrite a recorded hash" openspec/schemas/superspec/templates/verify.md
grep -n "changed hash must carry its note\|Every changed hash carries the note" openspec/schemas/superspec/templates/verify.md
grep -n "note condition" docs/AGENTS.md
```

Expected: the first command prints nothing; the second prints the sentence and checklist line; the third prints both divergence items.

- [ ] **Step 5: Stage the template and docs edits**

```bash
git add openspec/schemas/superspec/templates/verify.md docs/AGENTS.md
```

---

### Task 6: Whole-change verification and the single commit

**Files:**
- Test: the commands below; no edits expected

**Interfaces:**
- Consumes: Tasks 1–5, the recorded `<start>` revision, and the approved delta spec.
- Produces: the evidence that the change is complete, no old trigger remains, and one commit contains the whole policy change.

- [ ] **Step 1: Run the docs checks**

```bash
python3 docs/checks/check_agent_files.py --root .
python3 -m unittest discover -s docs/checks -p 'test_*.py' -v
```

Expected: the agent-file check exits 0 (warnings allowed); all unit tests pass.

- [ ] **Step 2: Run OpenSpec validation**

```bash
openspec validate --all
```

Expected: every item valid.

- [ ] **Step 3: Grep for every old trigger phrase**

```bash
grep -rn "Any edit.*reopens review\|A claimed cosmetic edit after the cycle\|confirmed cosmetic\|Normative edit after the cycle\|demands normative edits\|never rewritten to make it match\|a design edit also makes later approvals stale" \
  docs/superspec-workflow.md .agents/rules/repo-superspec-review-gate.md \
  openspec/schemas/superspec/schema.yaml openspec/schemas/superspec/templates/verify.md \
  docs/AGENTS.md openspec/changes/superspec-re-review-threshold/specs
```

Expected: no hits. The main spec still carries the old text until the closeout sync; it is not in this list.

- [ ] **Step 4: Check the rule size and the schema version**

```bash
wc -c .agents/rules/repo-superspec-review-gate.md
grep -n "^version:" openspec/schemas/superspec/schema.yaml
```

Expected: at most 4096 bytes; `version: 5`.

- [ ] **Step 5: Read the two sections end to end**

Read *Review passes and caps* and *Revision binding* once. Confirm the threshold is the only re-review trigger, the old cosmetic-confirmation and "any edit" paths are gone, every non-triggering path names the note, and the upstream qualification matches the confirmation rules. This is the read-through from `tasks.md` 4.6.

- [ ] **Step 6: Confirm the schema fork's skill rewrite is intact**

```bash
grep -rn "/opsx:" openspec/schemas
```

Expected: no hits. This is `tasks.md` 4.7.

- [ ] **Step 7: Inspect the full change against the starting revision**

```bash
git status --short
git diff --stat <start>
git diff <start>
```

Expected: only `docs/superspec-workflow.md`, `.agents/rules/repo-superspec-review-gate.md`, `openspec/schemas/superspec/schema.yaml`, `openspec/schemas/superspec/templates/verify.md` and `docs/AGENTS.md` changed (plus the change directory's planning files). No `.agents/skills/` or `.claude/skills/` path, no `version:` change, no model name.

- [ ] **Step 8: Make the single commit**

```bash
git add docs/superspec-workflow.md .agents/rules/repo-superspec-review-gate.md \
  openspec/schemas/superspec/schema.yaml openspec/schemas/superspec/templates/verify.md docs/AGENTS.md
git commit -m "docs(superspec): narrow the re-review threshold"
git status --short
```

Expected: one commit; the tree is clean.

- [ ] **Step 9: Hand off**

Report the commit, the checks and the review records. Do not archive and do not push unless the user asks; the normal verify, PR and closeout flow follows.
