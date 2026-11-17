## Why

Tracked work runs on a long agent procedure. The agent recovers the order from board position, writes every
status itself, and reconciles merged work only at the next intake. So the board is correct only while a
session runs: closing an issue by hand never promotes its dependents, Done depends on the agent remembering,
and nothing ever reaches Ready. Several ad-hoc steps also keep PRs, issues and milestones loosely linked.
Moving the bookkeeping to GitHub makes the board right without a session, shrinks what the agent must get
right to the judgment calls, and gives every later project the same setup through settings instead of
re-tuning the procedure.

## What Changes

**Who sets each status**
- From: the agent writes In Progress, In Review and Done, and recovers Done at the next intake.
- To: every status is derived from facts on GitHub, first match wins:
  1. issue closed → Done;
  2. the `blocked` label → Blocked;
  3. an open, non-draft PR from a trusted source that closes the issue → In Review (trusted: its head repository is the issue's repository, or its author is an owner, member or collaborator; other PRs are ignored and reported);
  4. an assignee → In Progress;
  5. every native blocker closed as completed → Ready, else → Backlog. A blocker closed as not planned or
     duplicate keeps the item in Backlog and is reported.

  The derivation is kept current by GitHub's built-in project workflows, a repository Action and the agent's
  intake. The agent's only status act is the claim: assign, then In Progress.
- Reason: the board stays correct without a session, and a stale write is repaired instead of sticking.
- Impact: Blocked is set by a label, assignment means "started", and pre-review PRs must be drafts.

**Next-task selection**
- From: the successor of the last completed item in board or view order, with many stop cases.
- To: the Ready, unassigned item with the lowest order value. Stop on a tie, a missing order value, an empty
  Ready set, or a project that is not enabled.
- Reason: board position and view settings cannot be read reliably; an order field can.
- Impact: board position stops mattering, and every open item needs an order value.

**Cross-references**
- PRs close their issue by closing keyword and are not added as project items.
- The Action copies the issue's milestone onto the PR.
- Follow-up issues are sub-issues with the parent's milestone and real blockers only. Their order value is
  placed after the parent's existing follow-ups.
- Milestones are feature-level, owned by planning, and reported (never closed) when empty.

**Follow-up work during a change**
- Work that no open decision blocks is done first. Small work is done in the same change; the threshold is
  the review gate's minor-edit limit.
- Larger work becomes a linked follow-up issue. Unclear cases go to the user.

**Session scope:** a session ends after the merge and the board updates. Starting the next task
automatically is out of scope.

**Enable switch**
- The repository variable `PROJECT_URL` names the one project the automation serves. It is set last, after
  setup.
- Until it is set, the Action and the agent's commands refuse that project.
- `check` reports setup facts and prose-dependency warnings separately.

**Packaging**
- A stdlib Python script shared by the Action and the agent.
- A `github-project` skill holding the "how" (commands and templates). The normative rules stay in
  `docs/superspec-workflow.md`, under the same headings the rules already cite.
- Shorter always-on rules that keep their required opening pointer.
- A generic setup guide.
- No project identifiers committed.

**Removed:** the order recovery from views and position, the agent-written In Review, and the
archived-`tracker.md` reconcile at intake. The old procedure is not kept as a fallback.

**This repository's board:** migrated as the last step of this change, after merge, with the user approving
each write.

## Capabilities

### New Capabilities
- `github-project-automation`:
  - the status derivation and how it is kept current (Action triggers, built-in workflows, convergent writes,
    claim collisions);
  - the shared script and its subcommands;
  - configuration and the enable switch;
  - the setup `check`;
  - the milestone copy;
  - follow-up issue creation and ordering;
  - the setup guide.

### Modified Capabilities
- `superspec-tracker-intake`:
  - next-task selection by order value instead of successor-of-last-completed;
  - the claim (assign, then In Progress) replaces the agent-written status transitions;
  - In Review is confirmed, not written;
  - the reconcile at intake is replaced by `sync`;
  - follow-up handling;
  - the commands move into the `github-project` skill. `tracker.md` stays.
- `superspec-closeout`:
  - finalize marks a draft PR ready for review and confirms In Review instead of setting it;
  - after the merge, closeout verifies the issue is closed and the item Done. It applies the manual fallback
    (close, then Done last) only when the automation missed it, and runs `sync` once.

## Impact

- **New files:**
  - `scripts/project_flow.py` with tests in `scripts/tests/`;
  - `.github/workflows/project-flow.yml`;
  - `.github/project-flow.json`;
  - `.agents/skills/github-project/` and its `.claude/skills/` link;
  - `docs/github-project-setup.md`.
- **Edited files:**
  - `.agents/rules/repo-tracker-intake.md` and `repo-superspec-closeout.md`;
  - the *Next task*, *tracker.md* and *Closeout* sections of `docs/superspec-workflow.md` (the headings stay);
  - the `superspec` schema fork (`schema.yaml`, `INTEGRATION.md`) and its divergence list in `docs/AGENTS.md`;
  - `.github/components.json` (ownership of the new files);
  - the workflow contract suites (`verify/test/workflow-contracts.test.mjs`, both `test_build_contract.py`
    suites) and the workflow scopes of `verify/check-remote-code.mjs`, which every new workflow must update in
    the same commit (the `ci-pipeline` requirement).
- **New credential:** a `PROJECT_TOKEN` repository secret that can write a user-owned project. Its minimum
  permissions are settled in the setup guide.
- **GitHub settings:** the built-in project workflows (*Item closed*, *Auto-add to project*) are enabled per
  project.
- **Users of the board:**
  - set Blocked with the label;
  - assign to mean "started";
  - edit the order value instead of dragging cards;
  - open pre-review PRs as drafts.
- **Unchanged constraints:**
  - the Action has no `edited` trigger, so `commit-conventions` is unchanged;
  - the rules keep their opening pointers, so `agent-guidance-layout` is unchanged.
- **No app, extension, crypto or protocol code changes.**
