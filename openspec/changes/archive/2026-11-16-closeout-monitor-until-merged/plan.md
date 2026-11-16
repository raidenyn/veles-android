# PR Finalization Closeout Rephrase Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rephrase the superspec closeout definition so monitoring ends at the pull request's merge, the repository's merge requirements are met while waiting, and the applicable follow-ups run after the merge.

**Architecture:** Policy stays in the small always-on closeout rule; the capability spec is the normative definition; `docs/superspec-workflow.md` carries the procedure; the superspec schema fork's finalize hand-off repeats the order. The change edits Markdown and YAML only, with no new workflow step.

**Tech Stack:** Markdown, YAML, the `gh` and `openspec` CLIs, and the standard-library agent-file check.

**Spec:** this change directory: `proposal.md`, `specs/superspec-closeout/spec.md`, and `tasks.md`.

## Global Constraints

- `.agents/rules/repo-superspec-closeout.md` stays at most 4,096 bytes and uses LF line endings.
- Keep unchanged: the merge prohibition, exactly-once sync and archive, comment trust rules, the issue-closed-first/Status-Done-last order, and the early-merge report with nothing marked Done.
- Use "monitor to merge" and "applicable follow-ups" consistently across the rule, spec, procedure and hand-off.
- No concrete project, project number, issue number, RFC path or field id appears in the rule, spec, procedure or schema files.
- Commit headers are `type(scope): subject`, at most 100 characters, with a lowercase scope; use `docs(superspec)` for these commits.

## Review Focus

1. A pull request with green checks but `mergeStateStatus: BEHIND` must trigger a branch update and a return to babysitting, not idle waiting.
2. A missing, unreadable or `UNKNOWN` merge state must be reported to the user, never treated as clear.
3. A merge that arrives before sync, archive and doc review must be reported with nothing marked Done.
4. A push or comment after the archive must not rerun sync or archive.
5. Untracked work merged after a completed closeout must make no issue or board write.

---

## Task 1: Rephrase the closeout rule

**Files:**
- Modify: `.agents/rules/repo-superspec-closeout.md`

**Interfaces:**
- Consumes: nothing.
- Produces: the rule wording the procedure and hand-off must match.

- [ ] **Step 1: Replace the fixed-order sentence**

Current first body paragraph after the read-first line:

```text
The order is fixed: babysit to green, sync and archive once, review the docs, push, babysit again, answer comments and wait for approval and merge, then the issue and the board.
```

Replace with:

```text
The order is fixed: babysit to green, sync and archive once, review the docs, push, babysit again, answer comments and monitor to merge, then the applicable follow-ups (the issue and the board for tracked work).
```

- [ ] **Step 2: Replace the "Keep waiting" bullet**

Replace:

```text
- **Keep waiting:** until merged, closed or the user says stop; never merge unless asked; prefer an event-driven watch, else poll coarsely with backoff and report only state changes.
```

with:

```text
- **Monitor to merge:** until merged, closed or the user says stop; meet the repository's merge requirements (for example update from master when required) and return to babysitting after any push; never merge unless asked; prefer an event-driven watch, else poll coarsely with backoff and report only state changes.
```

- [ ] **Step 3: Replace the "Done" bullet**

Replace:

```text
- **Done:** only after a merge and a completed closeout, with the issue closed first and Status written last.
```

with:

```text
- **After the merge:** do the applicable follow-ups — for tracked work close the issue first and write Status Done last; report a merge that came before the closeout finished, with nothing marked Done.
```

- [ ] **Step 4: Verify the rule text and size**

Run:

```bash
grep -n "wait for approval and merge\|Keep waiting\|- \*\*Done:\*\*" .agents/rules/repo-superspec-closeout.md
wc -c .agents/rules/repo-superspec-closeout.md
```

Expected: the grep prints nothing; `wc -c` is below 4096. The other bullets are byte-for-byte unchanged.

- [ ] **Step 5: Commit**

```bash
git add .agents/rules/repo-superspec-closeout.md
git commit -m "docs(superspec): monitor the closeout to merge"
```

## Task 2: Update the Closeout and Monitoring procedure

**Files:**
- Modify: `docs/superspec-workflow.md` (*Closeout* checklist, *Monitoring* entering, polling-cycle table and state handling)

**Interfaces:**
- Consumes: the rule order and terms from Task 1.
- Produces: the procedure text the rule cites.

- [ ] **Step 1: Replace checklist steps 6 and 7**

Replace:

```text
6. Answer comments and wait for approval and merge (below and *Monitoring*); never merge unless the user asks.
7. Update the issue and the board, Done last (below).
```

with:

```text
6. Answer comments and monitor to merge (below and *Monitoring*); meet the repository's merge requirements, such as a required update from master, and return to babysitting after any push; never merge unless the user asks.
7. Do the applicable follow-ups for merged work: update the issue and the board, Done last (below).
```

- [ ] **Step 2: Update "Entering monitoring mode"**

Replace the paragraph's first sentence list and endpoint with:

```text
Say what is being waited for — checks, review, approval, comments, the repository's merge requirements, the merge — and that the user can tell the agent to stop. Then watch, and keep waiting until the pull request is merged or closed or the user says stop; do not stop on your own because nobody has reviewed yet. Never merge the pull request unless the user asks.
```

Keep the following paragraphs on the event-driven watch, coarse polling with backoff, state-change reporting, no foreground sleep and no blocking unchanged.

- [ ] **Step 3: Add the merge state to the polling read**

In the polling-cycle table, replace the `gh pr view` row's command and supplies columns with:

```text
| `gh pr view <number> --json state,mergeStateStatus,headRefOid,statusCheckRollup,reviewDecision,reviews,comments` | lifecycle (`OPEN`, `MERGED`, `CLOSED`), merge state (`BEHIND`, `DIRTY`, `BLOCKED`, `CLEAN`, `UNKNOWN`), the current head commit, every check's name and conclusion, the review decision, reviews with their author and the commit they saw, conversation comments |
```

Leave the other table rows unchanged.

- [ ] **Step 4: Add merge-requirement handling and update the Merged bullet**

In *What to do with the state*, keep the **Checks** bullet and add after it:

```text
- **Merge requirements:** `BEHIND` means the branch must be updated from the base; update it (`gh pr update-branch`, or merge the base branch and push), then return to babysitting. A blocker the agent cannot resolve — including conflicts, a missing or unreadable merge state, or `UNKNOWN` — is reported to the user; do not wait on it silently.
```

Replace the **Merged** bullet with:

```text
- **Merged:** after a completed closeout, do the applicable follow-ups; if the merge came before sync, archive and doc review, report it and mark nothing Done.
```

Keep **Checks**, **Comments** and **Closed without merging** otherwise unchanged.

- [ ] **Step 5: Verify the procedure**

Run:

```bash
grep -n "wait for approval and merge" docs/superspec-workflow.md
grep -Eni "monitor to merge|mergeStateStatus|merge requirements|applicable follow-ups" docs/superspec-workflow.md
```

Expected: the first command prints nothing; the second hits the checklist (`monitor to merge`, `merge requirements`), entering monitoring mode (`merge requirements`), the polling table (`mergeStateStatus`) and the state handling (`merge requirements`, `applicable follow-ups`).

- [ ] **Step 6: Commit**

```bash
git add docs/superspec-workflow.md
git commit -m "docs(superspec): handle merge blockers while monitoring"
```

## Task 3: Update the finalize hand-off

**Files:**
- Modify: `openspec/schemas/superspec/templates/finalize.md`
- Modify: `openspec/schemas/superspec/schema.yaml`
- Modify: `openspec/schemas/superspec/INTEGRATION.md`

**Interfaces:**
- Consumes: the order and terms from Tasks 1 and 2.
- Produces: the next-step text that finalize hands to the closeout.

- [ ] **Step 1: Update both template bullets**

In `templates/finalize.md`, replace the `pr-created` and `pr-updated` lines with:

```text
- `pr-created`: "Monitor the PR to green, sync and archive once in the closeout, review the docs, push, then monitor to merge and complete the applicable follow-ups."
- `pr-updated`: "Monitor the PR to green, sync and archive once in the closeout, review the docs, push, then monitor to merge and complete the applicable follow-ups."
```

- [ ] **Step 2: Update the schema instruction and v5 description**

In `schema.yaml`, in the finalize instruction's next-step bullet, replace:

```text
           monitor to green, sync and archive once in the closeout,
           review the docs, push, wait for approval and merge.
```

with:

```text
           monitor to green, sync and archive once in the closeout,
           review the docs, push, monitor to merge and complete the
           applicable follow-ups.
```

In the same file's v5 description, replace "pushes, waits for approval and merges" with "pushes, monitors to merge and completes the applicable follow-ups".

- [ ] **Step 3: Update the integration step**

In `INTEGRATION.md` step 13, replace the final clause so the step ends:

```text
Then hand off to the closeout rule, which monitors the PR to green, syncs and archives once, reviews the docs, pushes, and monitors to merge, completing the applicable follow-ups.
```

- [ ] **Step 4: Verify the hand-off files**

Run:

```bash
grep -n "wait for approval and merge" .agents/rules/repo-superspec-closeout.md docs/superspec-workflow.md openspec/schemas/superspec/templates/finalize.md openspec/schemas/superspec/schema.yaml openspec/schemas/superspec/INTEGRATION.md
grep -En "monitors? to merge" .agents/rules/repo-superspec-closeout.md docs/superspec-workflow.md openspec/schemas/superspec/templates/finalize.md openspec/schemas/superspec/schema.yaml openspec/schemas/superspec/INTEGRATION.md
```

Expected: the first command prints nothing; the second hits the rule, the procedure, the template, the schema and the integration doc.

- [ ] **Step 5: Commit**

```bash
git add openspec/schemas/superspec/templates/finalize.md openspec/schemas/superspec/schema.yaml openspec/schemas/superspec/INTEGRATION.md
git commit -m "docs(superspec): hand off monitoring to merge"
```

## Task 4: Verify the definition agrees end to end

**Files:**
- No planned edits; fix any file the checks report and commit it with the matching task's message.

**Interfaces:**
- Consumes: Tasks 1 to 3.
- Produces: a green local check set and a reviewed definition.

- [ ] **Step 1: Run the docs node checks**

Run:

```bash
python3 docs/checks/check_agent_files.py --root .
python3 -m unittest discover -s docs/checks -p 'test_*.py' -v
```

Expected: the check exits 0 with no error finding for the changed rule, and every test passes.

- [ ] **Step 2: Validate OpenSpec**

Run:

```bash
openspec validate --all
```

Expected: the change and all specs are valid.

- [ ] **Step 3: Re-read the five Review Focus cases against the changed text**

Confirm: the branch-update scenario and the *Merge requirements* bullet cover case 1; the missing, unreadable and `UNKNOWN` report covers case 2; the *After the merge* rule bullet, the *Merged* procedure bullet and the spec's early-merge scenario cover case 3; the untouched *Sync and archive, exactly once* section covers case 4; the spec's untracked-work scenario and the rule's "for tracked work" qualifier cover case 5.

- [ ] **Step 4: Confirm the rule still points to the procedure**

Run:

```bash
head -5 .agents/rules/repo-superspec-closeout.md
wc -c .agents/rules/repo-superspec-closeout.md
```

Expected: the read-first line still cites *Closeout* and *Monitoring*; the file is below 4096 bytes.

- [ ] **Step 5: Commit any check fix**

If a check required a change, commit only the files it touched with a `docs(superspec): ...` header. Otherwise there is nothing to commit.
