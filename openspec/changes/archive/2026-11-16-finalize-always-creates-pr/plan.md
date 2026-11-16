# Finalize Always Creates the PR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make superspec finalize always create the code-review pull request when the feature branch has none,
without a prompt or skip choice, then continue into the closeout; a failed creation or confirmation stops
finalize with no recorded outcome.

**Architecture:** Policy stays in the always-on closeout rule; the capability spec is the normative
definition; `docs/superspec-workflow.md` carries the procedure; the superspec schema fork's finalize
instruction executes the order and its documentation repeats it. The change edits Markdown and YAML only, with
no new rule file and no new workflow step.

**Tech Stack:** Markdown, YAML, the `gh` and `openspec` CLIs, and the repository's standard-library agent-file
check.

**Spec:** `openspec/changes/finalize-always-creates-pr/specs/superspec-closeout/spec.md`; task breakdown in
`openspec/changes/finalize-always-creates-pr/tasks.md`.

## Global Constraints

- `.agents/rules/repo-superspec-closeout.md` stays at most 4,096 bytes and uses LF line endings.
- Keep unchanged: fail-closed required checks, bounded fixing, exactly-once sync and archive, comment trust,
  monitoring to merge without merging, and issue-closed-first/Status-Done-last.
- Use "create the PR when none exists" and "confirm it is open" consistently across the rule, spec, procedure
  and schema files.
- Do not rewrite the historical v3/v4 narratives in `openspec/schemas/superspec/README.md` or
  `openspec/schemas/superspec/INTEGRATION.md`; change only current-behavior passages.
- No concrete project, project number, issue number or field id appears in the rule, spec, procedure or
  schema files.
- Commit headers are `type(scope): subject`, at most 100 characters, using `docs(superspec)` for this change.

## Review Focus

1. A canonical-path passage that still offers, prompts for, or permits skipping PR creation — grep for the
   retired phrases and read each hit.
2. A receipt written or committed before the open-PR confirmation — read the finalize instruction's step order.
3. `kept-as-is` or `discarded` selected because no PR exists — read the escape-hatch text and both spec
   scenarios.
4. Tracker recording or In Review before the open-PR confirmation — read the finalize handoff and the closeout
   procedure.
5. A historical version narrative rewritten as if it were current — diff only current-behavior sections.

---

## Task 1: Rephrase the closeout rule

**Files:**
- Modify: `.agents/rules/repo-superspec-closeout.md`

**Interfaces:**
- Consumes: nothing.
- Produces: the rule wording the procedure, spec and schema handoff must match.

- [ ] **Step 1: Replace the opening line and fixed-order sentence**

Replace:

```text
Before closing out a pull request, read the sections Closeout and Monitoring of `docs/superspec-workflow.md`.

The order is fixed: babysit to green, sync and archive once, review the docs, push, babysit again, answer comments and monitor to merge, then the applicable follow-ups (the issue and the board for tracked work).
```

with:

```text
Before closing out, read the sections Closeout and Monitoring of `docs/superspec-workflow.md`.

The order is fixed: finalize creates the pull request when none exists, then babysit to green, sync and archive once, review the docs, push, babysit again, answer comments and monitor to merge, then the applicable follow-ups (the issue and the board for tracked work).
```

- [ ] **Step 2: Replace the Start bullet**

Replace:

```text
- **Start:** when finalize confirms an open pull request, tracked or not; the issue, board and `tracker.md` steps apply only to tracked work.
```

with:

```text
- **Start:** finalize always creates the pull request when the feature branch has none — never asking and never skipping — and reuses an existing pre-review PR; it confirms the PR is open, then writes the receipt with the true `pr-created` or `pr-updated` outcome, records the PR in `tracker.md` for tracked work, sets In Review and hands off. The closeout starts only from that open PR, tracked or not; the issue, board and `tracker.md` steps apply only to tracked work. A PR that cannot be created or confirmed stops finalize: report it, record no outcome and start no closeout.
```

- [ ] **Step 3: Replace the Local merge bullet**

Replace:

```text
- **Local merge:** ask before any push to the default branch; a rejected push is reported, not retried or bypassed, and a follow-up pull request is offered.
```

with:

```text
- **Local merge:** only when the user explicitly asks for that flow; ask before any push to the default branch; a rejected push is reported, not retried or bypassed, and a follow-up pull request is offered.
```

- [ ] **Step 4: Check the rule budget, encoding and line endings**

Run:

```bash
python3 -I -c "import pathlib; b=pathlib.Path('.agents/rules/repo-superspec-closeout.md').read_bytes(); b.decode('utf-8'); assert len(b) <= 4096, len(b); assert b'\r' not in b; assert b.endswith(b'\n'); print(len(b), 'bytes, UTF-8, LF')"
```

Expected: a byte count at most 4096, printing `... bytes, UTF-8, LF`.

- [ ] **Step 5: Commit**

```bash
git add .agents/rules/repo-superspec-closeout.md
git commit -m "docs(superspec): require finalize to create the PR"
```

---

## Task 2: Update the closeout procedure

**Files:**
- Modify: `docs/superspec-workflow.md` (*Closeout*, "When it starts")

**Interfaces:**
- Consumes: the rule wording from Task 1.
- Produces: the procedure text the schema handoff and documentation repeat.

- [ ] **Step 1: Replace the "When it starts" paragraph**

Replace:

```text
The closeout starts when the last implementation commit is pushed and finalize has confirmed an open pull
request, tracked or not. The steps that touch the board, the issue or `tracker.md` apply only to tracked work.
Then stay in monitoring mode as *Monitoring* describes.
```

with:

```text
The closeout starts when the last implementation commit is pushed and finalize has confirmed an open pull
request, tracked or not. Finalize always creates the pull request when the feature branch has none, reusing an
existing pre-review PR, and asks nothing; it then writes the receipt with the true `pr-created` or `pr-updated`
outcome, records the PR in `tracker.md` for tracked work, sets the card In Review and hands off. A pull request
that cannot be created or confirmed stops finalize with no outcome recorded. The steps that touch the board,
the issue or `tracker.md` apply only to tracked work. Then stay in monitoring mode as *Monitoring* describes.
```

- [ ] **Step 2: Check the rest of the section for an absent-PR path**

Run:

```bash
grep -n "no pull request\|no PR\|optional\|if one exists\|if a PR" docs/superspec-workflow.md
```

Expected: no canonical-path line that permits an absent PR; the only permitted hit is the explicit local-merge
escape hatch in *Closeout*.

- [ ] **Step 3: Commit**

```bash
git add docs/superspec-workflow.md
git commit -m "docs(superspec): state the PR is created before closeout"
```

---

## Task 3: Make the finalize instruction create the PR

**Files:**
- Modify: `openspec/schemas/superspec/schema.yaml` (top description, `finalize` artifact description,
  prerequisites, steps 8-13, comment subroutine reference, escape hatch, apply/verify PASS summaries)

**Interfaces:**
- Consumes: the behavior defined by the spec delta and Tasks 1-2.
- Produces: the executable finalize order every agent follows.

- [ ] **Step 1: Update the schema descriptions and prerequisites**

Replace the current-behavior phrases:

- the top description's "push the branch — which updates an existing spec pre-review PR if one was opened
  between plan and apply, or creates a remote tracking branch otherwise — and post a code-reviewer onboarding
  comment on the PR if one exists" with "push the branch, create the PR when none exists (an existing spec
  pre-review PR is reused), and post the code-reviewer onboarding comment";
- the v5 description sentence "finalize confirms the open pull request (`gh pr view`), records it in tracker.md
  for tracked work (committed and pushed as part of the PR), sets the card In Review and hands off to the
  closeout rule" with "finalize always creates the pull request when none exists, without asking and without a
  skip choice, confirms it open (`gh pr view`), writes the receipt with the true outcome, records it in
  tracker.md for tracked work (committed and pushed as part of the PR), sets the card In Review and hands off
  to the closeout rule";
- the `finalize` artifact description "merge worktree back into feature branch, push, optionally open PR, post
  code-reviewer comment" with "merge worktree back into feature branch, push, create the PR when none exists,
  post code-reviewer comment";
- the instruction opening "push the feature branch (which updates the existing PR if one was opened manually
  between plan and apply for spec pre-review, or creates a remote tracking branch if not), write the
  finalize.md receipt, and — if a PR exists — post a code-reviewer onboarding comment on it. The spec
  pre-review PR is an optional team-workflow pattern; the canonical closeout works either way." with "push the
  feature branch; the PR is created when none exists (an existing spec pre-review PR is reused), the finalize.md
  receipt is written, and a code-reviewer onboarding comment is posted on the PR";
- the optional-precondition paragraph "Optional precondition (recommended for team workflows): ... You can
  `gh pr create` after finalize completes, or never open a PR at all." with: "Pull request state: an open PR
  for this feature branch is optional at the start of finalize. This is the spec pre-review PR, opened
  manually between plan and apply so the logic reviewer can approve the proposal/specs/plan before code is
  written. If one exists, finalize reuses and updates it. If none exists, finalize creates the code-review PR
  itself, without asking; a branch without a PR never takes a non-canonical outcome."

- [ ] **Step 2: Rewrite the git-side steps 8-14 as this order**

Step 8 — Push the feature branch; an existing PR auto-updates and a branch without one gets its remote tracking
branch.

Step 9 — Read `PR_NUMBER` with `gh pr view "$FEATURE_BRANCH" --json number --jq .number`. If empty, read
`proposal.md`, infer the Conventional Commit type (unchanged rules: `feat` default, `fix`, `refactor`, `docs`,
`test`, `chore`), build `<type>(<scope>): <change-name>` or `<type>: <change-name>`, and run `gh pr create`
with the existing body template. Never prompt for a title and never offer a skip. If creation reports that a
PR already exists, reuse it through `gh pr view`; any other failure is reported and finalize stops with no
outcome. If `PR_NUMBER` is non-empty, reuse it and create no duplicate.

Step 10 — Confirm the PR with `gh pr view "$FEATURE_BRANCH" --json state,url,number`. A missing PR or a state
other than `OPEN` is an error: report it, record no outcome and stop; do not hand off.

Step 11 — Run the comment-posting subroutine and keep its result for the receipt.

Step 12 — Write `finalize.md` per `templates/finalize.md` with the true outcome — `pr-created` when step 9
created the PR, `pr-updated` when it reused one — the PR URL, branch state and comment status. Keep the branch,
worktree, cleanup, test-baseline and next-step values from the existing receipt block; map Final state to the
Outcome: `pr-created` records Final state `pr-open`, and `pr-updated` records Final state `pr-updated`. Write
no receipt before the confirmation in step 10 succeeds.

Step 13 — Commit the receipt and push it.

Step 14 — Hand off: for tracked work record the PR in `tracker.md`, commit and push that edit as part of the
PR, then set the item to In Review (matched by name; stop and ask when the field or an option is missing), then
hand off to `.agents/rules/repo-superspec-closeout.md`.

State that `merge-locally`, `kept-as-is` and `discarded` are only for an explicitly user-chosen non-canonical
flow, and a missing PR is never a reason to select one.

- [ ] **Step 3: Update the comment subroutine reference and the escape hatch**

Change "invoked from the git-side closeout (step 12)" to "invoked from the git-side closeout (step 11)". In the
escape-hatch paragraph, state that its hand-written receipt may use any outcome the user chose, but "no
pre-review PR" is not an escape-hatch reason because the canonical flow creates the PR. Change both
escape-hatch handoff references — "Hand off per step 13's outcome branch" and "Once finalize.md exists, follow
step 13" — to step 14, preserving their per-outcome branches.

- [ ] **Step 4: Update the apply and verify PASS summaries**

In both PASS summaries, replace "update the PR if one exists, post code-reviewer comment if a PR is present"
and "update the PR if one exists" with "create the PR when none exists and update it, post the code-reviewer
comment". Keep the archive-once and escape-hatch sentences that follow.

- [ ] **Step 5: Check the retired canonical phrases**

Run:

```bash
grep -n "Offer to open\|PR creation prompt\|skip PR creation\|No PR exists\|optionally open PR\|updates PR if one exists\|comment if PR exists" openspec/schemas/superspec/schema.yaml
```

Expected: no hits, except any line inside an explicitly historical v3/v4 narrative (none are expected in
`schema.yaml`).

- [ ] **Step 6: Commit**

```bash
git add openspec/schemas/superspec/schema.yaml
git commit -m "docs(superspec): create the PR in the finalize instruction"
```

---

## Task 4: Align the schema documentation and template

**Files:**
- Modify: `openspec/schemas/superspec/templates/finalize.md`
- Modify: `openspec/schemas/superspec/README.md`
- Modify: `openspec/schemas/superspec/INTEGRATION.md`

**Interfaces:**
- Consumes: the instruction order from Task 3.
- Produces: no executable behavior; keeps the fork's documentation truthful.

- [ ] **Step 1: Add the canonical-PR note to the template**

Under the Outcome line in `templates/finalize.md`, add:

```markdown
> Canonical finalize always leaves an open pull request: record `pr-created` or `pr-updated`.
> `merge-locally`, `kept-as-is` and `discarded` are explicit non-canonical flows only.
> Outcome `pr-created` records Final state `pr-open`; `pr-updated` records `pr-updated`.
```

- [ ] **Step 2: Update the README's current-behavior passages**

In the workflow table's Endpoint row, replace "if a PR exists, post code-reviewer onboarding comment" with
"create the PR when none exists and update it, post the code-reviewer onboarding comment". In both usage-flow
comments, replace "updates PR if one exists" and "posts code-reviewer comment if PR exists" with "creates the
PR when none exists and updates it" and "posts the code-reviewer comment". Do not change the historical v3/v4
sections.

- [ ] **Step 3: Update the INTEGRATION canonical sequence**

Rewrite the "Canonical git-side closeout sequence" list as steps 1-14: tests, merge, cleanup and branch delete
as today, then step 8 push, step 9 create the PR when none exists and reuse an existing one, step 10 confirm it
open and stop on failure, step 11 post or edit the comment, step 12 write the receipt with the true outcome,
step 13 commit and push the receipt, step 14 record the PR in `tracker.md` for tracked work, set In Review and
hand off. Update the DAG description, the golden-path step 2, and the paragraph that calls the spec pre-review
PR optional so they say the canonical flow creates the code-review PR when none exists. Replace the "no-PR
variant" paragraph with: the canonical path has no no-PR variant; only the explicit escape hatch can leave a
branch without a PR. In the escape-hatch section, replace the note "Note: \"no pre-review PR opened\" is NOT an
escape-hatch case — the canonical git-side closeout handles it (push creates the remote tracking branch, comment
subroutine self-skips)" with "Note: \"no pre-review PR opened\" is NOT an escape-hatch case — the canonical
git-side closeout creates the code-review PR and posts the comment."

- [ ] **Step 4: Check current-versus-historical edits**

Run:

```bash
git diff -- openspec/schemas/superspec/README.md openspec/schemas/superspec/INTEGRATION.md
```

Expected: current-behavior passages changed; the v3/v4 narrative sections untouched.

- [ ] **Step 5: Commit**

```bash
git add openspec/schemas/superspec/templates/finalize.md openspec/schemas/superspec/README.md openspec/schemas/superspec/INTEGRATION.md
git commit -m "docs(superspec): describe the mandatory finalize PR"
```

---

## Task 5: Record the fork divergence

**Files:**
- Modify: `docs/AGENTS.md` (divergence item 3)

**Interfaces:**
- Consumes: Tasks 3-4.
- Produces: the reinstall instruction that keeps the mandatory PR step after a schema reinstall.

- [ ] **Step 1: Extend divergence item 3**

Replace the item text with:

```markdown
3. **Finalize hand-off**: the `finalize` instruction's always-create PR step (no prompt, no skip), the
   open-PR confirmation and the fail-closed stop, the receipt written only after confirmation, the
   `tracker.md` record (committed and pushed as part of the PR) and the In Review step, the per-outcome
   hand-off to `.agents/rules/repo-superspec-closeout.md`, and the matching "Next step" bullets in
   `templates/finalize.md`.
```

- [ ] **Step 2: Commit**

```bash
git add docs/AGENTS.md
git commit -m "docs(superspec): record the mandatory PR divergence"
```

---

## Task 6: Verify the change

**Files:**
- No new files; verify all modified files.

**Interfaces:**
- Consumes: Tasks 1-5.
- Produces: evidence for the verify artifact.

- [ ] **Step 1: Search for retired canonical phrasing**

Run:

```bash
grep -rn "Offer to open\|PR creation prompt\|skip PR creation\|No PR exists\|optionally open PR\|updates PR if one exists\|comment if PR exists" \
  .agents/rules/repo-superspec-closeout.md docs/superspec-workflow.md docs/AGENTS.md \
  openspec/schemas/superspec/schema.yaml openspec/schemas/superspec/templates/finalize.md \
  openspec/schemas/superspec/README.md openspec/schemas/superspec/INTEGRATION.md
```

Expected: no hits outside the historical v3/v4 narrative sections of README/INTEGRATION.

- [ ] **Step 2: Re-read the order end to end**

Read the rule, spec delta, procedure, schema instruction, template, README and INTEGRATION together. Confirm:
the PR is created when none exists; an existing PR is reused; no prompt or skip remains; the receipt is
written only after confirmation; tracker and In Review follow confirmation; a failed creation or confirmation
stops with no outcome; all three escape-hatch outcomes require an explicit user choice.

- [ ] **Step 3: Run the agent-file check and its tests**

```bash
python3 docs/checks/check_agent_files.py --root .
python3 -m unittest discover -s docs/checks -p 'test_*.py' -v
```

Expected: exit 0 for both.

- [ ] **Step 4: Validate all OpenSpec artifacts**

```bash
openspec validate --all
```

Expected: exit 0.

- [ ] **Step 5: Run the repository's pre-push node checks**

```bash
python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)
```

Run the checks listed in the `AGENTS.md` of every node it selects, including `docs` for this change.

- [ ] **Step 6: Commit a verification fix only if one was required**

If Step 2 or a check required an edit, stage only the edited files explicitly and commit them:

```bash
git add <edited files>
git commit -m "docs(superspec): fix finalize verification findings"
```

Otherwise skip this step.
