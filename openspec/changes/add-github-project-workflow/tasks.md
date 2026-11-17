## 0. Ground rules for every task

- [ ] 0.1 Work red → green: for every script and check task, write the failing test first and commit it with its implementation. The workflow-contract negative tests come before the workflow. No test sleeps or reads the wall clock (`repo-no-time-based-tests`). All GitHub access in tests goes through the fake transport.
- [ ] 0.2 Before any fixture is recorded, ask the user for a scratch repository and a scratch project under their account, and for permission to use them for fixtures and for the end-to-end run. Stop if this is refused; nothing in this change may use the real board or this repository's issues before merge.

## 1. Fixtures and transport

- [ ] 1.1 In the scratch project from 0.2, create the minimal data needed and record real GraphQL and REST response fixtures under `scripts/tests/fixtures/project_flow/`. Cover:
  - paginated project items;
  - Status and number fields;
  - blockers (closed as completed, not planned and duplicate);
  - closing PRs (draft and ready);
  - assignees and labels;
  - sub-issues at two depths;
  - repository milestones;
  - the project's `workflows`.

  Strip tokens, and replace owners, logins and ids with neutral placeholders.
- [ ] 1.2 Tests first, then `scripts/project_flow.py`. Its base: the injectable transport (one `gh api` path, plus a fake transport that records every mutation), config loading from `.github/project-flow.json`, the role map matched by name, and named exit reasons. Stdlib only, runnable as `python3 -I`.
- [ ] 1.3 Tests first, then complete paginated reads with fail-closed results per item: a partial read means unreadable, not empty. Tests: multi-page items, a failing page, and a partial blocker read on one item while the others still reconcile.

## 2. Status derivation and reconciliation

- [ ] 2.1 Tests first (one per `Every status is derived from facts on GitHub` scenario), then the derivation rules 0–4. Report blockers closed as not planned or duplicate. Ignore draft-issue and PR items. A missing Status option stops with no write.
- [ ] 2.2 Tests first (every `Reconciliation is complete, idempotent and fails closed` scenario: dropped event, PR opened and merged between runs, conflicting milestones, PR already holding a milestone, empty milestone, partial read, second run makes no write), then `sync`:
  - the derivation over every item, writing only on a difference;
  - before each write, a fresh read of that item's inputs, skipping the write and reporting when they changed;
  - the milestone copy for open and merged PRs that close project issues;
  - the empty-milestone report;
  - never creating, closing or renaming a milestone, field or option.
- [ ] 2.3 Tests first (the two-repository fixture), then the caller scopes:
  - in Action mode, writes only to the items and PRs of the Action's own repository, while reading blockers, parents and milestones from anywhere;
  - in agent mode, the whole enabled project.
- [ ] 2.4 Interleaving tests through a transport that changes facts between a read and a write. Cases:
  - a stale Ready after `start` has read back its own write;
  - a stale write over Blocked;
  - a stale write over Done;
  - Backlog demotion racing a claim;
  - a PR event racing a `sync`;
  - two accounts claiming one item.

  Each converges on the next `sync`.
- [ ] 2.5 Dry-run tests for every writing command (`sync`, `next`'s initial reconciliation, `start` and every `followup` step): the fake transport records zero mutations, and the output lists each write that would happen. This includes the two-change `sync --dry-run` scenario.

## 3. Enable switch, configuration and check

- [ ] 3.1 Tests first, then `.github/project-flow.json` and its offline validation. The file holds the Status field name, the role → option map, the order field name and the blocked label name, with defaults matching the existing board's names. Validation rejects any id, number, owner or URL in it.
- [ ] 3.2 Tests first, then the enable gate: read the `PROJECT_URL` repository variable. `sync`, `next`, `start` and `followup` refuse with a named reason when it is unset, unreadable (reporting the fix), or names a different project.
- [ ] 3.3 Tests first, then `check`. With no project, it validates the config offline. With a project, it reports:
  - **Facts:** disabled built-in workflows, a missing order field or Status option, open items without an order value, In Progress items without an assignee, Blocked items without the label.
  - **Heuristic warnings:** prose dependency mentions without a native link.

  The output says that a clean warning list proves nothing and that setting `PROJECT_URL` is the attestation.

## 4. Selection, claim and follow-ups

- [ ] 4.1 Tests first (every scenario of the requirement), then `next`: run `sync`, then pick the Ready, unassigned open issue with the lowest order value. Stops: not enabled, nothing Ready, a tie (naming both items), an incomplete read, and `missing-order` (listing the items). `--skip-unordered` continues past unordered items.
- [ ] 4.2 Tests first, then `start <issue>`:
  1. derive the status afresh;
  2. refuse anything not Ready (naming the open blockers for Backlog) unless `--force` is given;
  3. add the assignee with the add-assignees operation;
  4. set In Progress;
  5. re-read the assignees and stop on a collision with another login;
  6. reconcile the item.

  Tests cover the claim, a Backlog item refused, `--force`, and the collision.
- [ ] 4.3 Tests first, then `followup` with its six ordered steps:
  1. create the issue, printing its number immediately (the body links the PR, the change directory and where the work was found);
  2. attach it as a sub-issue;
  3. set the milestone;
  4. add the given blockers only, refusing the parent as a blocker (with a test that passes the parent in the list);
  5. add it to the project;
  6. set the order value.

  On a failure after step 1, print the issue number and the names of the missing steps. Then implement `--resume <issue>` with the original arguments: complete only the missing steps, and stop without writing when an existing link disagrees. Tests: failure after creation then resume, and a disagreeing resume.
- [ ] 4.4 Tests first, then the order algorithm (`n` = lowest value above `p` held by an item that is not a descendant of the parent, or `p + 1` if there is none; `v` = max in `[p, n)`; first of +0.1/+0.01/+0.001 below `n` and unused; compare at 3 decimals). Tests:
  - 15 → 15.1 → 15.2;
  - 15.1 with sibling 15.2 → 15.11;
  - no unrelated successor (`n = p + 1`);
  - no room left;
  - a parent without a value;
  - an incomplete descendant read stops the allocation.

## 5. The Action

- [ ] 5.1 Write the negative contract tests first: in `verify/test/workflow-contracts.test.mjs`, and in the `test_build_contract.py` suites under `native-bridge/host/scripts/` and `native-bridge/installer/scripts/` as their scope requires. They fail if `project-flow.yml` checks out the PR head, triggers on `edited`, installs a package at run time, or reads a token other than `PROJECT_TOKEN`. Add the workflow to the workflow scopes of `verify/check-remote-code.mjs`.
- [ ] 5.2 Add `.github/workflows/project-flow.yml`:
  - **triggers:** `issues` (closed, reopened, labeled, unlabeled, assigned, unassigned); `pull_request_target` (opened, reopened, ready_for_review, converted_to_draft, closed); daily `schedule`; `workflow_dispatch`;
  - **checkout:** the base branch only, and no PR code is executed;
  - **credentials:** the token comes only from `PROJECT_TOKEN`, and the job permissions are minimal;
  - **actions:** pinned following the repository's existing workflow conventions;
  - **concurrency:** one group with `cancel-in-progress: false`;
  - **runs:** every run executes `sync` in Action mode and writes a job summary with the changes, the reports and the GraphQL rate-limit cost;
  - **setup:** a successful no-op with a notice when `PROJECT_URL` or `PROJECT_TOKEN` is absent;
  - **checks:** not a required check.

  The 5.1 tests pass in the same commit.
- [ ] 5.3 Update `.github/components.json` so the `tooling` node owns `.github/workflows/project-flow.yml` and `.github/project-flow.json`, and run `python3 -I scripts/components.py check`.

## 6. Agent guidance, schema and docs

- [ ] 6.1 Rewrite the *Next task* and *tracker.md* sections of `docs/superspec-workflow.md` as the normative rules. The headings stay. The sections cover:
  - **Scope:** tracked only when the user names the project or issue as the source; a project named as an example is not tracked; issue-only intake without an enabled project is untracked throughout.
  - **Issue-only intake:** explicit repository resolution, and asking among several projects.
  - **Selection:** `next` by order value, and the agent's response to each stop reason (`missing-order` asks whether to `--skip-unordered`; the others ask the user to name the task).
  - **Announcing:** the chosen task and why, then reading the issue's links into the brainstorm.
  - **Named tasks:** a named task that is not Ready is described and asked about before any claim; `--force` is passed only after the user explicitly authorizes it.
  - **Resume:** detecting a resume from an active `tracker.md`.
  - **The claim**, made after `tracker.md`.
  - **The derivation and convergence summary.**
  - **Finalize outcomes:** `discarded` asks whether to release the assignment or add the blocked label; `kept-as-is` keeps the claim.
  - **Follow-ups:** the follow-up rule.

  Remove the successor procedure and the archived-`tracker.md` reconcile. Name no model and no project.
- [ ] 6.2 Update *Closeout* and *Monitoring* in `docs/superspec-workflow.md`:
  - finalize marks the draft PR ready for review, adds the closing keyword, and confirms In Review after one `sync` without writing it, reporting any other derived status;
  - the post-merge check: close the issue with a comment if the merge did not, `sync` once, confirm Done, and report newly Ready items and the follow-ups;
  - the **local-merge path:** after a successful push, close the issue with a comment naming the pushed commit, then `sync` to derive Done; on a rejected or declined push, the issue stays open and the claim stays;
  - the stop report promises no later reconcile and lists the unfinished steps.
- [ ] 6.3 Add `.agents/skills/github-project/SKILL.md` and the `.claude/skills/github-project` symlink. The skill holds the commands only:
  - the `gh` scope precheck;
  - the `project_flow.py next`, `start`, `sync` and `check` invocations;
  - the resume check against active `tracker.md` files;
  - the PR-body template with the closing keyword and the follow-up list;
  - the `followup` and `--resume` usage;
  - the post-merge and local-merge commands.

  It cites the doc sections and restates no rule. The agent-file check already verifies the link for every skill and compares only `openspec-*` skills against the documented list, so no check change is expected. Confirm by running it.
- [ ] 6.4 Edit `.agents/rules/repo-tracker-intake.md`: keep its opening instruction to read *Next task* and *tracker.md*; replace the bullets with the scope rule, "run the steps through the `github-project` skill", the no-identifiers rule and the missing-scope rule; stay under the 4,096-byte `repo-*` cap.
- [ ] 6.5 Edit `.agents/rules/repo-superspec-closeout.md`: finalize marks the draft PR ready for review and confirms In Review without writing it; the post-merge check replaces "close the issue first, Done last"; the local merge closes the issue and syncs; the stop report promises no later reconcile; keep the opening pointer and the byte cap.
- [ ] 6.6 Edit `openspec/schemas/superspec/schema.yaml` and `INTEGRATION.md`:
  - spec pre-review PRs open as drafts;
  - finalize marks the PR ready for review, confirms it is open and not a draft, puts the closing keyword in the PR body, records the PR in `tracker.md`, runs one `sync` and confirms In Review without writing it, reporting any other derived status;
  - the local-merge step closes the issue and syncs instead of setting Done;
  - `templates/finalize.md`: keep the order of the next steps, and replace its "set Done" wording with "close the issue if the merge did not, then run `project_flow.py sync`".

  Record the divergence in the fork list in `docs/AGENTS.md`.
- [ ] 6.7 Write `docs/github-project-setup.md`, naming no concrete project. It covers:
  - the fields and Status options, and how to map their names in the config;
  - the built-in workflows to enable (*Item closed*; *Auto-add to project* filtered to issues only);
  - `PROJECT_URL`, and `PROJECT_TOKEN` with a placeholder for its minimum verified permissions (filled in 8.2);
  - the conventions: assignment means started, Blocked is the label, order is a field, pre-review PRs are drafts, milestones are feature-level;
  - converting prose dependencies;
  - the ordered enable steps ending with setting `PROJECT_URL`;
  - rollback by unsetting `PROJECT_URL`.

  Link it from `scripts/AGENTS.md` and add the `project_flow.py` commands to that node's Build & Test list.

## 7. Local checks

- [ ] 7.1 Prime the offline run: run `./gradlew installGitCliff scriptsTest` once with the network on, so the pinned git-cliff and the Gradle caches exist. Then run `./gradlew --offline scriptsTest` with the network disabled.
- [ ] 7.2 Run:
  - `python3 -I docs/checks/check_agent_files.py --root .` and its unit tests;
  - `openspec validate --all`;
  - the verify workflow-contract and remote-code suites;
  - both `test_build_contract.py` suites;
  - `python3 -I scripts/components.py check`.
- [ ] 7.3 Grep for concrete project numbers, issue numbers, project URLs, owners, RFC paths and node ids, and expect none. Generic placeholders such as `<owner>` and `#<n>` are allowed, and so are clearly marked examples in specs and tests. Search these files:
  - the rules, the skill and `openspec/config.yaml`;
  - the schema fork, `.github/project-flow.json` and `docs/superspec-workflow.md`;
  - the setup guide.

  Also grep the rules for their opening pointer sentences, and check that every section they cite exists in `docs/superspec-workflow.md`.
- [ ] 7.4 Run the pre-push node selection (`python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)`) and run the checks of every selected node.

## 8. Real-environment verification (before the PR is marked ready for review)

- [ ] 8.1 In the scratch repository from 0.2, copy in the workflow, config and script. Set up the scratch project with the setup guide: fields, options, built-in workflows and the `PROJECT_TOKEN` secret, with `PROJECT_URL` set last.
- [ ] 8.2 Settle the minimum token type and permissions empirically: try a fine-grained token on the user-owned project, and fall back to a classic PAT with `project`. Fill the result into the setup guide, then re-run 7.2–7.4.
- [ ] 8.3 Drive the full loop through the real Action:
  - Backlog ↔ Ready on blocker add and close (and not-planned);
  - a claim with `start`, and a refused Backlog claim;
  - draft PR → ready → In Review → back to draft → In Progress;
  - the blocked label on and off;
  - a merge with `Closes #` → issue closed → Done;
  - the milestone copy, including a PR opened and merged between runs;
  - the empty-milestone report;
  - `followup` plus `--resume`;
  - `next` on a tie, `missing-order` and an empty Ready set;
  - an unset `PROJECT_URL` making the Action a no-op.

  Write the observed outcomes to `verification-notes.md` in the change directory.
- [ ] 8.4 Do not touch the real board or this repository's issues before merge. Put the scratch repository and project and each covered transition with its outcome in the PR description. Only then may finalize mark the PR ready for review.

## 9. Post-merge: apply to this repository's board (closeout follow-up)

- [ ] 9.1 With the user approving each write:
  - enable the built-in workflows (auto-add filtered to issues only);
  - add `PROJECT_TOKEN`;
  - convert the prose dependencies the agent proposes to native links;
  - fill in missing order values;
  - move manual Blocked statuses to the blocked label.
- [ ] 9.2 Run `check` until its facts are clean and the user has reviewed its warnings. Then the user sets `PROJECT_URL`, and `sync` runs once with its report shown to the user. List the milestone re-planning for the user; do not automate it.
