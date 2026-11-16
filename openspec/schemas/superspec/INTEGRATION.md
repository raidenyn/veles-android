# OpenSpec × Superpowers Integration Guide

> This document explains how the `superspec` schema integrates OpenSpec's artifact governance workflow with Superpowers' execution skills into a single workflow. It serves as a reference table for new member onboarding, change reviews, and as required reading before modifying the schema.
>
> Corresponding schema version: `superspec` v5

---

## 1. The Nature of the Integration: What Goes Where

OpenSpec handles **"WHAT"** — governance, validation, and archival of markdown artifacts like proposal / specs / design / tasks.
Superpowers handles **"HOW"** — execution skills such as brainstorming conversations, TDD discipline, subagent dispatch, code review, etc.

The two are integrated through a custom schema [schema.yaml](./schema.yaml). The integration is not at the code level — instead, OpenSpec artifact instructions contain directives like "at this step, use the Skill tool to invoke `superpowers:xxx`." **No superpowers skill files are modified**, nor is the OpenSpec CLI — the integration is purely at the instruction layer.

---

## 2. Overview of the 7 Superpowers Touch Points

| # | Superpowers skill | Where it hooks in | Trigger method |
|---|---|---|---|
| 1 | `superpowers:brainstorming` | `brainstorm` artifact instruction | Direct |
| 2 | `superpowers:writing-plans` | `plan` artifact instruction | Direct |
| 3 | `superpowers:using-git-worktrees` | apply step 1 | Direct |
| 4 | `superpowers:subagent-driven-development` | apply step 2a | Direct |
| 5 | `superpowers:test-driven-development` | (auto-triggered inside #4) | **Transitive** (SKILL.md L205 / L274) |
| 6 | `superpowers:requesting-code-review` | (auto-triggered inside #4) | **Transitive** (SKILL.md L270) |
| 7 | `superpowers:finishing-a-development-branch` | Manual escape hatch only — the git-side closeout is executed by the schema directly (v4) | **Fallback** |

There is also one **fallback**:

- `superpowers:executing-plans` (apply step 2b) — only used when the current platform lacks subagent support. On Claude Code, always use 2a. Per `superpowers:executing-plans` SKILL.md L14: "If subagents are available, use `superpowers:subagent-driven-development` instead of this skill."

---

## 3. Artifact DAG (with Superpowers Injection Points)

```text
┌──────────────┐
│  brainstorm  │ ◄── superpowers:brainstorming
│  (root)      │     (2-3 approaches + Alternatives Considered)
└──────┬───────┘
       │
       ├──► ┌──────────┐
       │    │ proposal │    Why (50-1000 chars) / What Changes / Capabilities
       │    └────┬─────┘
       │         │
       │         ▼
       │    ┌──────────────────┐
       │    │ specs/**/*.md    │    ADDED / MODIFIED / REMOVED / RENAMED
       │    │ (delta specs)    │    Each requirement includes SHALL/MUST + scenario
       │    └────┬─────────────┘
       │         │
       │         ▼
       │    ┌──────────┐
       │    │  tasks   │    Coarse-grained checkboxes (tracking vehicle for apply)
       │    └────┬─────┘
       │         │
       │         ▼
       │    ┌──────────┐
       │    │  plan    │ ◄── superpowers:writing-plans
       │    └────┬─────┘     (2-5 minute micro-steps)
       │         │
       │         ▼
       │    ┌──────────┐
       │    │  apply   │ ◄── superpowers:using-git-worktrees
       │    │ (DAG +   │ ◄── superpowers:subagent-driven-development
       │    │  apply:  │         ├── superpowers:test-driven-development (transitive)
       │    │  phase)  │         └── superpowers:requesting-code-review (transitive)
       │    │ writes   │
       │    │ apply.md │
       │    └────┬─────┘
       │         │
       ▼         ▼
    ┌──────────┐ ┌──────────┐
    │  design  │ │  verify  │ ◄── openspec-verify-change (6 checks)
    │(optional)│ └────┬─────┘
    └──────────┘      │
                      ▼
                  ┌──────────┐
                  │ finalize │ ◄── schema-executed git-side closeout (v4)
                  │          │     writes finalize.md + creates or updates the PR
                  └──────────┘
                      │
                      ▼
                  /openspec-archive-change  (not in DAG; the closeout runs it once)
```

**Key points**:

- `design` is an **optional leaf**. Brainstorm still attempts to pre-populate design.md, but tasks no longer hard-depend on it (`tasks.requires: [specs]`). Per OpenSpec conventions: `design.md` is only written when non-trivial technical decisions need explanation.
- `apply` is a **real DAG node** as of schema v2. It generates `apply.md` (a minimal receipt — iteration counter, worktree, branch, commit range, task counts) so the DAG can honestly express "verify depends on apply having run." The canonical `/openspec-apply-change` instruction body still lives in the top-level `apply:` phase block; the apply artifact's own instruction is a short redirect to avoid drift.
- `verify` requires `apply` (was `plan` in v1). The OpenSpec CLI will refuse to surface verify as a `ready` artifact until `apply.md` exists.
- `finalize` is a **real DAG node** as of schema v3. It generates `finalize.md` (a minimal git-closeout receipt: outcome, PR URL, final branch state) and requires `verify`. `/openspec-continue-change` surfaces finalize's instruction after verify completes. As of schema v4 (see §4 Step 4 and §6 Design Choice #6), that instruction executes the git-side closeout directly (merge worktree → feature branch, push the branch, create the PR when none exists and update an existing one, and post the code-reviewer comment); `superpowers:finishing-a-development-branch` is retained as a manual escape hatch only. As of schema v5, finalize then confirms the open PR, records it in `tracker.md` for tracked work (committed and pushed as part of the PR), sets the card In Review and hands off to the closeout. `/openspec-archive-change` is the lifecycle close that the closeout runs exactly once, when the required checks are green; it is not in the DAG (it remains an OpenSpec CLI command).
- The convergence loop (apply → verify → loop back on code-fixable FAILs, capped at 5 iterations) is documented in `docs/workflow-details.md`. The schema enforces the file-existence dependency; the iteration decision is made by the agent or by a future loop-runner command (not in scope for v2 or v3).

---

## 4. Complete Development Workflow (Lifecycle of a Single Change)

### Step 0: Decide Whether to Use the Change Process

Ask yourself: is this a behavioral change?

| Type | Requires a change? | Which schema |
|---|---|---|
| New feature / new capability | Yes | `superspec` |
| Breaking change | Yes | `superspec` |
| Architecture change | Yes | `superspec` |
| Bug fix (restoring original behavior) | No | Direct PR |
| Adding/backfilling tests | No | Direct PR |
| Build tool tweaks (linter rules, coverage thresholds, etc.) | No | Direct PR |
| Non-breaking dependency upgrades | No | Direct PR |
| Documentation updates | No | Direct PR |

This decision logic is documented in the "When Not to Create a Spec" section of [openspec/specs/README.md](../../specs/README.md).

---

### Step 1: Create a Change + Enter Brainstorming

```bash
/openspec-new-change my-feature --schema superspec
# → Creates openspec/changes/my-feature/ empty directory + .openspec.yaml
# → Displays brainstorm artifact instructions
```

Then:

```bash
/openspec-continue-change
# → Triggers the brainstorm artifact
# → Instruction says "use the Skill tool to invoke superpowers:brainstorming"
# → Enters multi-turn interactive conversation: context exploration → clarify questions → 2-3 approaches + trade-offs → approve design
# → After conversation ends, writes brainstorm.md (with Alternatives Considered)
# → If design artifacts were produced, simultaneously writes design.md (pre-populated)
```

**Key**: This step is the alignment ceremony for the entire workflow. All subsequent proposal / specs are distilled from brainstorm.md.

---

### Step 2: Sequentially Produce proposal → specs → tasks → plan

You can `/openspec-continue-change` step by step (with human review opportunity at each step), or `/openspec-ff-change` to fill in all remaining artifacts at once.

| Step | Output | Key rules |
|---|---|---|
| 2a | `proposal.md` | Why section 50-1000 chars; Capabilities section lists new/modified capabilities |
| 2b | `specs/<capability>/spec.md` | 4 delta types (ADDED / MODIFIED / REMOVED / RENAMED); each requirement includes SHALL/MUST + `#### Scenario:` |
| 2c (opt) | `design.md` | Only written when technical decisions need explanation; brainstorm may have already pre-populated it |
| 2d | `tasks.md` | Coarse-grained checkboxes (`- [ ] X.Y description`), tracked during apply |
| 2e | `plan.md` | `/openspec-continue-change` triggers `superpowers:writing-plans`, breaking tasks into 2-5 minute micro-steps |

After completion, run:

```bash
openspec validate --all --json
# → A local git hook is already set up as pre-commit, automatically validating on commit
```

---

### Step 3: Apply (Implementation Phase)

```bash
/openspec-apply-change
```

This triggers the steps in [schema.yaml](./schema.yaml) `apply.instruction`:

#### 3-0. Pre-flight — Commit change artifacts to the current branch first

Before creating the worktree, confirm that `openspec/changes/<name>/` is tracked on the current branch. If still untracked (`git status --porcelain` output contains `??`), **commit only that change directory** (do not use `git add -A`) as `docs(openspec): scaffold <name> change`.

**Why this step is needed**: The worktree branches off from the current branch; if the change directory is still untracked on main, merging the worktree back to main later will hit an "untracked files would be overwritten by merge" error. This step separates "planning phase artifacts" and "implementation phase artifacts" into two commits, ensuring the main branch never has a drifting untracked copy.

#### 3-1. Workspace — Invoke `superpowers:using-git-worktrees`

- Creates an isolated workspace at `.worktrees/<change-name>/`
- Switches to a new branch
- Runs project setup, confirms clean test baseline

#### 3-2. Executor — Invoke `superpowers:subagent-driven-development` (2a default path)

- Main agent reads plan.md, dispatches a **fresh subagent** for each micro-task
- Each subagent automatically:
  - **Enforces TDD** (`superpowers:test-driven-development` triggered transitively)
    - Write a failing test first
    - Watch it fail
    - Write the minimum code to make it pass
    - No test before production code? Delete and redo
  - **Per-task code review** (`superpowers:requesting-code-review` triggered transitively)
    - Spec compliance review (does it match the plan?)
    - Code quality review (any smells?)
    - Critical issues block progress
- Updates `tasks.md` checkboxes as coarse tasks complete
- After all tasks finish, runs a final code review on the entire implementation

> **2b fallback**: Only use `superpowers:executing-plans` when the current platform lacks subagent support. Claude Code has subagents, so always use 2a. If forced to use 2b, you must manually maintain TDD discipline and invoke `superpowers:requesting-code-review`.

#### 3-3. Receipt — Write `apply.md`

Before invoking verify, the executor writes a minimal `apply.md` receipt per `openspec/schemas/superspec/templates/apply.md`: change name, iteration counter (1 on first apply, incremented on re-entry), applied-at timestamp, executor identity, worktree path, branch, commit range, and `X of Y` tasks completed. This is the v2 DAG artifact that satisfies `verify.requires: [apply]`. If `apply.md` already exists in the change directory, read its `Iteration:` field, increment by one, and overwrite the file.

#### 3-4. Verification — Invoke `openspec-verify-change` (produces `verify.md`)

6 checks:

1. **Structural validation**: `openspec validate --all --json` all PASS
2. **Task completion**: All `- [ ]` in `tasks.md` changed to `- [x]`
3. **Delta spec sync state**: Has `changes/<name>/specs/` been synced to `openspec/specs/`?
4. **Design / specs coherence**: Spot-check that design decisions and spec requirements are consistent (non-blocking warning)
5. **Implementation signal**: No unstaged files in the worktree
6. **Review records**: Every planning file has an approved, current record from every reviewer its record header lists; every recorded hash (the file's own and each upstream file's) matches the file on disk; the upstream set is the full set; no critical or high finding is open

If any check fails, go back to the corresponding artifact, fix it, and re-run verify.

---

### Step 4: Finalization

`/openspec-continue-change` after verify completes surfaces the `finalize` artifact's instruction. As of schema v4, the instruction executes **the git-side closeout** directly — it does NOT invoke `superpowers:finishing-a-development-branch` in the canonical flow.

#### 4-1. Canonical git-side closeout sequence (executed by the schema)

1. Detect worktree path and worktree branch name; detect the feature branch.
2. Verify tests pass in the worktree.
3. Switch to the feature branch in the main checkout, pull.
4. `git merge --ff-only <worktree-branch>` into the feature branch.
5. Re-verify tests on the merged result.
6. Worktree cleanup with provenance guard (only for paths under `.worktrees/`, `worktrees/`, or `~/.config/superpowers/worktrees/`).
7. Delete the local worktree branch.
8. `git push origin <feature-branch>` — an existing PR auto-updates with the merge commits and the implementation history; a branch without one gets its remote tracking branch.
9. Create the code-review PR when none exists: `gh pr view` reports no PR number, so read `proposal.md`, derive the Conventional Commit title (`<type>(<scope>): <change-name>`, default `feat`) and run `gh pr create` with the standard body. Never prompt for a title and never offer a skip; if creation reports that a PR already exists, reuse it through `gh pr view`, and any other failure is reported with no outcome recorded.
10. Confirm the PR is open (`gh pr view --json state,url,number`). A missing PR or a state other than `OPEN` is an error: report it, record no outcome and stop.
11. Post (or edit in place) a single code-reviewer onboarding comment on the PR, summarizing the change for a reviewer who did not see the spec pre-review, and keep its result for the receipt.
12. Write `finalize.md` on the feature branch with the true outcome — `pr-created` when step 9 created the PR, `pr-updated` when it reused one — plus the PR URL, branch state and comment status. No receipt is written before step 10 succeeds.
13. Commit the receipt on the feature branch and push it.
14. For tracked work record the PR in `tracker.md`, commit and push that edit as part of the PR, and set the card In Review. Then hand off to the closeout rule, which monitors the PR to green, syncs and archives once, reviews the docs, pushes, and monitors to merge, completing the applicable follow-ups.

The comment uses a marker (`<!-- superspec:finalize-comment -->`) for idempotent upsert; re-running finalize edits the existing comment rather than duplicating it. The body is **summarized in the agent's own words** from the change artifacts; verbatim paste is forbidden.

An open spec pre-review PR is **optional at the start of finalize** — recommended for teams that want a logic-review checkpoint before code is written. If one exists, finalize reuses and updates it; if none exists, finalize creates the code-review PR itself, without asking and without a skip choice. The canonical git-side closeout always leaves an open PR, so a branch without one never takes a non-canonical outcome.

#### 4-2. Escape hatch (manual skill invocation)

If your workflow doesn't match the git-side closeout — e.g. solo merge-to-main (Option 1), brand-new PR from the skill (Option 2), keep worktree alive for iteration (Option 3), or discard (Option 4) — invoke `superpowers:finishing-a-development-branch` directly via the Skill tool, pick the matching option, hand-write `finalize.md` from `templates/finalize.md`, and then run the comment-posting subroutine (defined in the finalize instruction). The subroutine self-skips when there's no PR and posts/edits the orientation comment when one exists. For a `pr-created` or `pr-updated` outcome the escape hatch must first confirm the PR is open (`gh pr view --json state,url,number`); a missing or non-open PR is reported and stops with no outcome. Note: "no pre-review PR opened" is NOT an escape-hatch case — the canonical git-side closeout creates the code-review PR and posts the comment.

#### 4-3. Retrospective (recommended, non-blocking)

Same as v3 — write a short `retrospective.md` in the change directory before `finalize.md` if the change is non-trivial. Six suggested sections: Wins, Misses, Plan deviations, Skill/workflow compliance, Surprises, Promote candidates.

---

### Step 5: Archive (once, inside the closeout)

The closeout runs this exactly once, when every required check of the pull request is green:

```bash
/openspec-archive-change my-feature
```

Behavior:

- Validates + checks task completion (incomplete tasks warn but don't block)
- Syncs delta specs back to `openspec/specs/<capability>/spec.md`
  - Order: RENAMED → REMOVED → MODIFIED → ADDED
  - If already manually synced, use `--skip-specs`
- Moves `changes/my-feature/` to `changes/archive/YYYY-MM-DD-my-feature/`
- History is frozen; the unix timeline is treated as the source of truth

`/openspec-archive-change` does NOT merge git branches and does NOT create PRs. The git-side closeout is the `finalize` artifact's responsibility (Step 4-1 above).

#### Canonical PR-review golden path (v5)

```text
1. verify completes (verify.md committed on feature branch in the worktree)
2. finalize (the git-side closeout — schema merges worktree → feature
   branch, pushes, creates the code-review PR when none exists and
   reuses an existing spec pre-review PR, confirms it open, posts the
   code-reviewer onboarding comment, then finalize.md records the true
   outcome and Final state; worktree is removed during finalize; for
   tracked work the PR is recorded in tracker.md and the card set In
   Review; then finalize hands off to the closeout)
3. The closeout monitors the PR to green (its required checks on the
   current head commit)
4. Sync and archive once (openspec-sync-specs, then
   /openspec-archive-change; new commits land on the feature branch)
5. Review the documentation the sync and archive produced
6. Push the sync, archive and doc commits to update the PR, then
   monitor again
7. [PAUSE: human code review on the PR; reviewer approves]
8. Answer comments and wait for approval; the user merges
   (gh pr merge --squash --delete-branch or GitHub UI)
9. Update the issue and the board, Done last
```

This golden path applies whether or not the team opened a spec pre-review PR: finalize creates the code-review PR when none exists. The canonical path has **no no-PR variant** — only the explicit escape hatch can leave a branch without a PR, and only when the user chooses a non-canonical flow such as a local merge, whose closeout runs the sync, archive once and doc review and asks before any push to the default branch.

The archive-before-merge ordering keeps the PR's diff complete: every commit that went into the change (implementation, finalize.md, archive sync) is in the PR. If the PR is merged before archive runs, the archive commits would have to be authored on main after the fact — recoverable but loses the unified audit trail. Note that step 7 from v3 ("local worktree cleanup if still present") is no longer needed in the git-side closeout because the worktree is removed during finalize itself.

#### Local-merge variant (acceptable for solo / local-only changes)

If the user falls back to the escape hatch and picks the skill's Option 1 (Merge locally), the skill performs the merge into the integration branch inline and removes the worktree. The closeout then runs the sync, archive once and doc review on the integration branch, asks the user before any push to the default branch, and sets Done only after that push succeeds. This inverts the archive/merge order vs. the canonical git-side closeout path. Acceptable for solo or local-only changes where the PR audit trail isn't relevant.

---

## 5. CLI Cheat Sheet

| Scenario | Command |
|---|---|
| **After first clone** | `bash scripts/install-git-hooks.sh` |
| New change (interactive, step by step) | `/openspec-new-change <name> --schema superspec` then `/openspec-continue-change` several times |
| New change (auto-fill all artifacts at once) | `/openspec-ff-change <name>` |
| Resume an interrupted change | `/openspec-continue-change <name>` |
| Enter implementation | `/openspec-apply-change <name>` |
| Manual verify | `/openspec-verify-change <name>` |
| Archive (once, inside the closeout, when the required checks are green) | `/openspec-archive-change <name>` |
| Use the native OpenSpec schema (skip brainstorm) | `/openspec-new-change <name> --schema spec-driven` |
| View all project schemas | `openspec schemas` |
| View current change progress | `openspec status --change <name> --json` |
| List active changes | `openspec list` |
| Full project validation | `openspec validate --all --json` |

---

## 6. Elegant Design Choices in the Integration (6 Worth Remembering)

### 1. Output redirection

Superpowers' brainstorming normally writes to `docs/superpowers/specs/`, and writing-plans writes to `docs/superpowers/plans/`. Our artifact instructions **override this behavior** by injecting "write to the change directory" directives via prompt context. No superpowers source code is changed, and no OpenSpec CLI changes are needed.

### 2. Schema-level vs prompt-level integration

The integration happens entirely in the `instruction` field (pure prompt). If superpowers upgrades a skill's behavior, we **don't need to touch the schema at all**. The schema.yaml only needs updating if a skill is renamed or removed.

### 3. Making transitive dependencies explicit

TDD and code-review are originally hidden inside subagent-driven-development (only visible in its SKILL.md). The schema **explicitly lists** these two transitive activations in the apply step 2a instruction, so readers can immediately understand "what exactly happens during the apply phase."

### 4. Honest fallback path labeling

2b (executing-plans) exists but is labeled as a "platforms without subagent support" fallback, citing the official superpowers SKILL.md L14 verbatim. We don't invent custom rules like "use 2b for small changes."

### 5. Apply is a real artifact, not a hidden phase (v2)

In schema v1, `verify.requires: [plan]` was a deliberate lie — the comment said "this edge exists only for the graph; actually verify must run after apply." That was unenforceable: agents read the DAG, saw verify reachable as soon as plan was done, and ran verify before apply.

In v2, `apply` is promoted to a real artifact (generating `apply.md`, a minimal receipt) so `verify.requires: [apply]` is honest. The schema graph and the actual sequencing now agree. The top-level `apply:` block is preserved so `/openspec-apply-change` continues to surface the canonical worktree+subagent instruction body — the apply artifact's own instruction is a short redirect to keep a single source of truth.

This change also unlocks the documented apply → verify → repeat convergence loop, since `verify.md` outcomes can now feed cleanly back into a re-run of apply with an incremented iteration counter. See `docs/workflow-details.md` for the loop pattern.

### 6. Finalize is a real artifact, not a hidden phase (v3)

In v2, post-verify git closeout (PR creation, worktree cleanup) was reachable only via the apply: block's step 5 prose, which an agent that just ran `/openspec-verify-change` typically doesn't re-read. The verify artifact's instruction said "proceed" on PASS without naming the next call site, and verify.md's convergence-loop reminder only covered FAIL paths. As a result, agents (and humans) routinely jumped straight to `/openspec-archive-change`, skipping `superpowers:finishing-a-development-branch` and leaving the branch and PR unfinished.

In v3, `finalize` is promoted to a real DAG artifact (`generates: finalize.md`, `requires: [verify]`). `/openspec-continue-change` surfaces its instruction after verify completes; that instruction invokes `superpowers:finishing-a-development-branch` and records the outcome. No new slash command is needed — the existing OpenSpec workflow vocabulary already gives the call site.

This change also moves the recommended retrospective guidance from the apply: block (where it was misplaced — apply ends with verify, not with archive) into finalize's instruction, where it logically belongs as a pre-archive activity.

**v4 addendum — schema owns the git-side closeout's logic.**

Promoting finalize to an artifact (v3) made the call site discoverable but kept the executor delegated to `superpowers:finishing-a-development-branch`. That skill's 4-option menu doesn't fit Superspec's PR-pre-review workflow: its "Option 2: push and create PR" creates a *new* PR from the worktree branch, leaving the user's pre-existing feature branch (with the artifacts and any open pre-review PR) orphaned, and its Option 1 ("Merge locally") merges into the integration branch instead of the feature branch. In v4, the finalize instruction executes the git-side closeout directly — merge worktree → feature branch, push the branch (which updates the spec pre-review PR if one exists, or creates a remote tracking branch otherwise), and post a code-reviewer comment when a PR is present — and demotes the skill to a manual escape hatch for the truly off-canonical flows (solo merge-to-main, brand-new PR via the skill's Option 2, keep-as-is, discard). The schema-executed closeout handles both the "PR exists" and "no PR yet" cases cleanly without escape-hatching — the comment subroutine self-skips when there's no PR. The schema borrows two narrow pieces from the skill (worktree-provenance guard, test-verify → merge structural pattern) with explicit attribution and a documented recreation method; everything else lives in the schema.

### Migration from schema v1

If a project pinned to schema v1 has in-flight changes whose `verify.md` was authored before v2 landed but no `apply.md` exists in the change directory, `/openspec-verify-change` will report the change as blocked under v2 (missing required artifact `apply`).

Migration: author a minimal `apply.md` by hand from `openspec/schemas/superspec/templates/apply.md` — set `Iteration: 1`, fill the worktree path, branch, commit range, and task counts from the existing implementation, then re-run `/openspec-verify-change`. This is a one-time migration cost per in-flight change; no archived changes are affected.

### Migration from schema v2

If a project pinned to schema v2 has in-flight changes that already have `verify.md` but no `finalize.md`, `/openspec-continue-change` under v3 will report finalize as the next ready artifact and refuse to advance further. Migration:

1. Author a minimal `finalize.md` by hand from `openspec/schemas/superspec/templates/finalize.md`. Fill the fields from the actual branch state — pick the outcome that matches what was already done (e.g., `pr-created` if a PR exists), copy branch state from `git status` / `gh pr view`, and record current worktree state.
2. Re-run `/openspec-continue-change`; it should now advance past finalize, and the change is ready for `/openspec-archive-change`.

This is a one-time migration cost per in-flight v2 change; no archived changes are affected.

### Migration from schema v3

In-flight v3 changes that already have `finalize.md` are unaffected — the file exists and `/openspec-continue-change` advances past finalize. Re-running finalize on those changes is optional.

For v3 changes mid-flight with no `finalize.md`:

1. `/openspec-continue-change` under v4 will surface the new git-side closeout instruction. If your workflow matches the git-side closeout (feature branch + worktree), the schema executes the git-side closeout automatically — no manual finalize.md authoring needed. This works whether or not you opened a spec pre-review PR between plan and apply; when a PR exists it gets updated and the onboarding comment is posted, when no PR exists the push creates the remote tracking branch and the comment subroutine self-skips.
2. If a structural prerequisite is missing (you started on the integration branch, or are in detached HEAD), use the escape hatch documented in the finalize instruction.

If you previously ran the v3 finalize and ended up with the two-branch schism (an orphan branch on remote and a PR from a different branch), the recommended cleanup is:
- Decide which branch is the real PR. Usually that's the worktree-named branch.
- Delete the orphan branch on the remote: `git push origin --delete <orphan>`.
- Re-run finalize under v4 to ensure the receipt and comment are in place. (Or hand-write finalize.md and run only the comment subroutine.)

This is a one-time migration cost per in-flight v3 change with a schism; archived changes are unaffected.

---

## 7. Recommended Snapshot Section for Projects Adopting This Schema

It's recommended that every project adopting `superspec` maintains a snapshot in the following format in the relevant repo document, so new members can see "what this repo currently looks like" at a glance during onboarding:

```markdown
## Project Status (snapshot: YYYY-MM-DD)

- **OpenSpec CLI**: v<version>
- **Schema**: `superspec` v<n>
- **Specs (bounded-context granularity)**: <n> domains exist, <n> domains reserved for lazy backfill
  - Existing: `<capability-a>` / `<capability-b>` / ...
  - Reserved: `<capability-c>` / ...
- **Automation**: <what openspec commands pre-commit / CI runs>
- **Superpowers plugin**: `superpowers@<version>` installed at `<path>`, this integration uses N skills
```

> This snapshot section will become stale over time; for authoritative state, query live with `openspec list` + `openspec schemas`.

---

## 8. The Most Important Takeaway

The core value of the integration is not "chaining many skills together" — it is:

> **Connecting "requirements alignment" (OpenSpec) with "rigorous execution" (Superpowers) so that the entire path from "what we want to do" to "code that has passed TDD + code review" is fully traceable, reproducible, and auditable for a single change.**

The break points in traditional workflows are:

- Requirements live in Slack / conversations → during apply the LLM works from memory → doesn't match the spec
- Or: spec is written in Confluence → code is in the repo → the two drift apart

The two-layer constraint of Superspec solves this problem:

1. **OpenSpec's delta spec governance** → ensures "what to do" doesn't drift
2. **Superpowers' subagent-driven + TDD + review** → ensures "what was done" has quality discipline

Put another way: OpenSpec is responsible for **rescuing requirements from conversations**, and Superpowers is responsible for **rescuing discipline from human willpower**. Only when combined do they form complete spec-driven development.

---

## Related Documents

- [schema.yaml](./schema.yaml) — Machine-readable definition of this schema
- [README.md](./README.md) — Design motivation and high-level overview of the schema
- [templates/](./templates/) — Markdown templates for each artifact
- [../../specs/README.md](../../specs/README.md) — Capability domain classification guide
- [openspec-conventions spec](https://github.com/Fission-AI/OpenSpec/blob/main/openspec/specs/openspec-conventions/spec.md) — Official OpenSpec conventions
- [obra/superpowers](https://github.com/obra/superpowers) — Superpowers skill source
