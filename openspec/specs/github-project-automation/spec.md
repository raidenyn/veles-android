# github-project-automation Specification

## Purpose
This capability defines how the statuses of a GitHub Project's issues are derived from facts on GitHub (the issue state, the blocked label, pull requests from a trusted source, assignees and native blockers) by one stdlib script, how a repository Action and the agent keep them current without exclusive writes, how a task is selected, claimed and followed up, and how a project is enabled, checked and verified end to end, without naming any concrete project.

## Requirements

### Requirement: Every status is derived from facts on GitHub
For each project item whose content is an issue, `scripts/project_flow.py` SHALL derive the Status only from facts on GitHub, never from the item's previous Status. The first matching rule wins:

0. the issue is closed → Done;
1. the issue carries the blocked label → Blocked;
2. at least one open, non-draft pull request from a trusted source closes the issue → In Review;
3. the issue has an assignee → In Progress;
4. every native "blocked by" issue is closed as completed, or there is none → Ready, otherwise → Backlog.

A pull request is from a trusted source when its head repository is the issue's repository, or its author association is OWNER, MEMBER or COLLABORATOR (a pull request without a head repository counts only through the association). A pull request that is not from a trusted source MUST NOT change the derived Status, MUST NOT receive a milestone write, and MUST be reported. A blocker closed as not planned or as a duplicate MUST NOT count as closed, and the item MUST be reported. Status options MUST be matched by name through the configured role map. Draft-issue and pull-request items on the project MUST be ignored. A write MUST be made only when the derived Status differs from the current one.

#### Scenario: Last blocker closes
- **WHEN** an open, unassigned, unlabelled issue with no closing PR has its last open blocker closed as completed
- **THEN** its item becomes Ready

#### Scenario: Blocker abandoned
- **WHEN** an item's only blocker is closed as not planned
- **THEN** the item stays Backlog and is reported

#### Scenario: Blocker added to a Ready item
- **WHEN** a native blocker that is open is added to a Ready item
- **THEN** the next reconciliation makes it Backlog

#### Scenario: Outsider fork PR
- **WHEN** a non-draft pull request from a fork by an author with no association closes an issue that is Ready
- **THEN** the issue stays Ready, `next` can still select it, the pull request receives no milestone write, and the report names the ignored pull request

#### Scenario: Collaborator fork PR
- **WHEN** a non-draft pull request from a fork by a collaborator closes an assigned issue
- **THEN** the item becomes In Review

#### Scenario: Draft PR
- **WHEN** the only PR that closes an assigned issue is a draft
- **THEN** the item is In Progress, and it becomes In Review when the PR is marked ready for review

#### Scenario: PR closed unmerged
- **WHEN** the only ready-for-review closing PR of an assigned issue is closed without merging
- **THEN** the item returns to In Progress

#### Scenario: Blocked by label
- **WHEN** the blocked label is added to an In Progress issue
- **THEN** the item becomes Blocked, and it is derived again from the remaining facts when the label is removed

#### Scenario: Closed issue
- **WHEN** an issue is closed by any means
- **THEN** its item is Done

#### Scenario: Status option missing
- **WHEN** the project's Status field has no option with a configured name
- **THEN** the script stops with a named reason and writes nothing

### Requirement: Reconciliation is complete, idempotent and fails closed
`project_flow.py sync` SHALL perform the complete reconciliation in one run:
- the status derivation for every item of the project;
- the milestone copy for every open or merged pull request that closes an issue on the project;
- the report of every open repository milestone that has no open issue and at least one closed issue on the project.

It MUST print a line `sync: complete` when every item was read completely and no read, conflict or write problem was reported, and otherwise a line `sync: partial (<n> unreadable, <m> conflicts, <k> skipped)` (exit 0 for a partial run unless a write failed); consumers that report or confirm Statuses from a run MUST do so only after `sync: complete`. An item whose blockers the token cannot see (the reported blocker count exceeds the blockers read) MUST be treated as unreadable, never as having no blocker. It MUST print one line `ready: <owner>/<repo>#<n> order=<value>` for every item that is Ready after the reconciliation, whoever wrote that Status, with `order=-` for an item without an order value; the post-merge report of newly Ready tasks is built from these lines and the merged issue's blocking list, never from this run's writes. When the project query exposes it, `sync` MUST finish with a line `rate-limit: cost=<total> remaining=<last>`. It MUST attempt every write and report every failed write; a failed write MUST make `sync` finish with the named stop `write-failed` (exit 3) after all reports and completed writes were printed, and MUST make `next` stop with `write-failed` instead of selecting after a failed reconciliation. `start` writes its claim (the assignment and In Progress) before it reconciles the claimed item, so when that reconciliation reports a failed write `start` MUST stop with `write-failed` and MUST NOT announce a successful claim; the completed claim writes stay in place and are not rolled back.

It MUST follow every paginated connection it reads to its last page. An item whose facts cannot be read completely MUST receive no write and MUST be reported. Running `sync` twice with no change on GitHub in between MUST make no write in the second run. `sync` MUST NOT close, create or rename a milestone, field or Status option.

#### Scenario: Dropped event
- **WHEN** the run for a PR's opened event was replaced by a later pending run
- **THEN** the later run still derives the PR's issue as In Review and copies the milestone onto the PR

#### Scenario: PR opened and merged between runs
- **WHEN** a PR opened and merged with no run in between
- **THEN** the next run copies the issue's milestone onto the merged PR

#### Scenario: Conflicting milestones
- **WHEN** a PR closes two issues with different milestones, or the issue's milestone name does not exist in the PR's repository
- **THEN** no milestone is set and the case is reported

#### Scenario: PR already has a milestone
- **WHEN** a PR already has a milestone
- **THEN** it is not changed

#### Scenario: Empty milestone
- **WHEN** the last open issue of a milestone is closed
- **THEN** the milestone is reported as complete and is not closed

#### Scenario: Partial read
- **WHEN** the blockers of one item cannot be read to the last page
- **THEN** that item is not written and is reported, and the other items are reconciled

#### Scenario: Failed write
- **WHEN** one item's Status write is rejected while another item's write succeeds
- **THEN** both writes are attempted and printed, `sync` ends with `stop: write-failed` and exit 3, and `next` selects nothing

#### Scenario: Ready summary
- **WHEN** an item was already made Ready by the Action before this run
- **THEN** this run's `ready:` lines still list it

#### Scenario: Second run
- **WHEN** `sync` runs twice with no change in between
- **THEN** the second run makes no write

### Requirement: Each caller reconciles a defined scope
The Action SHALL reconcile, and write to, only the project items whose issue lives in the Action's own repository, and only the pull requests of that repository. It MAY read blockers, parents and milestones from any repository. The agent's `sync` and `next` SHALL cover every item of the enabled project, using the user's own `gh` credentials; `start` reconciles only the claimed item. A project that spans repositories SHALL install the Action in each repository; the item sets of those Actions are disjoint.

#### Scenario: Project spanning two repositories
- **WHEN** the Action runs in repository A of a project that also holds issues from repository B
- **THEN** it writes only items whose issue is in A, while blockers in B are still read when deriving A's items

#### Scenario: Agent intake on the same project
- **WHEN** the agent runs `next` on that project
- **THEN** it reconciles items from both repositories before selecting

### Requirement: A task has one card, linked to its pull requests
The flow SHALL keep exactly one project item per task: the issue. The script MUST NOT add a pull request to the project, and the setup guide MUST configure *Auto-add to project* to add issues only. A pull request SHALL be linked to its issue only by a closing keyword in its body, so that it appears in the item's *Linked pull requests* field and closes the issue on merge.

#### Scenario: PR opened
- **WHEN** a PR with `Closes #15` is opened
- **THEN** no project item is created for the PR, and issue 15's item lists it under *Linked pull requests*

### Requirement: Concurrent writers converge
Because GitHub Projects offers no compare-and-set, the design SHALL make status writes convergent, not exclusive. A stale write from any writer MUST be undone by the next reconciliation. An assigned item MUST never be selected by `next`, whatever its Status says. The unit tests MUST drive these interleavings through a transport that changes the facts between a read and a write:
- a stale Ready landing after `start` has read its own write back;
- a stale write landing over Blocked;
- a stale write landing over Done;
- Backlog demotion racing a claim;
- a PR event racing a `sync`;
- two accounts claiming one item.

#### Scenario: Stale Ready after a claim
- **WHEN** a `sync` that read the item before the claim writes Ready after `start` assigned it and set In Progress
- **THEN** `next` does not select the item, and the next `sync` sets In Progress again

#### Scenario: Stale write over Blocked
- **WHEN** a stale write sets Ready on an item that carries the blocked label
- **THEN** the next `sync` sets Blocked again

### Requirement: The next task is the lowest-ordered Ready, unassigned item
`project_flow.py next` SHALL first run `sync` and then select the Ready, unassigned, open-issue item with the lowest value in the configured order field. It MUST NOT read views, view filters or board position. It MUST stop with a named reason and select nothing when:
- the project is not the enabled one;
- no item is Ready;
- the lowest value is shared by more than one candidate;
- the items or their facts could not be read completely;
- a Ready, unassigned item has no order value. The stop reason is `missing-order`, and the output lists those items.

After the user agrees, `next --skip-unordered` SHALL select among the remaining candidates by the same rule. It MUST still stop on every other reason.

#### Scenario: Ordinary pick
- **WHEN** Ready, unassigned items have order values 16, 17 and 22.5
- **THEN** the item with value 16 is selected, wherever its card sits on the board

#### Scenario: Tie
- **WHEN** two Ready, unassigned items share the lowest value
- **THEN** `next` stops and names both

#### Scenario: Nothing Ready
- **WHEN** no item is Ready after `sync`
- **THEN** `next` stops and says so

#### Scenario: Missing order value
- **WHEN** a Ready, unassigned item has no order value
- **THEN** `next` exits non-zero with `missing-order` and lists the item

#### Scenario: Continue past unordered items
- **WHEN** the user agrees and the agent runs `next --skip-unordered`
- **THEN** the lowest-ordered remaining candidate is selected

### Requirement: A task is claimed by assignment
`project_flow.py start <issue>` SHALL derive the item's Status afresh and MUST refuse, with a named reason, any item that is not Ready unless `--force` is given. The skill MUST pass `--force` only after the user explicitly authorizes it. It SHALL then:
1. add the authenticated user as an assignee with the add-assignees operation, never replacing existing assignees;
2. set In Progress;
3. re-read the assignees;
4. reconcile that item.

If the re-read shows any login other than the authenticated user, `start` MUST report a collision and stop. Two simultaneous claims by the same account are not detected; this is accepted because every session is started by the user.

#### Scenario: Claim
- **WHEN** `start` runs on a Ready item
- **THEN** the issue is assigned to the authenticated user and the item is In Progress

#### Scenario: Backlog item named
- **WHEN** `start` runs on a Backlog item without `--force`
- **THEN** it refuses and names the open blockers

#### Scenario: Two accounts
- **WHEN** a second account is assigned between the claim and the re-read
- **THEN** `start` reports a collision and stops

#### Scenario: Failed claim reconciliation
- **WHEN** `start` has assigned the issue and set In Progress but the reconciliation of that item reports a rejected write
- **THEN** `start` ends with `stop: write-failed` and exit 3, announces no successful claim, and leaves every completed write (the assignment, the status and any other write that succeeded) in place, with no rollback

### Requirement: Follow-up issues are created fully linked and resumable
`project_flow.py followup` SHALL create a follow-up issue in this order:
1. create the issue, with a body linking the PR, the change directory and the place where the work was found, and print the new issue number immediately;
2. attach it as a sub-issue of the originating issue;
3. set the originating issue's milestone;
4. add native "blocked by" links only to the issues it was given, never to its parent;
5. add it to the project;
6. set its order value.

The order value MUST be computed as follows:
- let `p` be the parent's value;
- let `n` be the lowest value above `p` held by an item that is not a sub-issue, at any depth, of the parent, or `p + 1` if there is none;
- let `v` be the largest value in `[p, n)`;
- the new value is the first of `v + 0.1`, `v + 0.01` and `v + 0.001` that is below `n` and unused, with values compared after rounding to three decimals.

If the parent has no order value, or no step fits, `followup` MUST stop and ask before writing the value. `followup --resume <issue>` with the original arguments MUST complete only the missing steps, MUST NOT create a second issue or duplicate a link, and MUST stop without writing when an existing link disagrees with its arguments.

#### Scenario: First and second follow-up
- **WHEN** issue 15 gets a follow-up and then another, and the next unrelated item is 16
- **THEN** they get 15.1 and 15.2

#### Scenario: Nested follow-up
- **WHEN** 15.1 gets a follow-up while its sibling 15.2 exists
- **THEN** it gets 15.11

#### Scenario: Failure after creation
- **WHEN** adding the item to the project fails after the issue was created and attached
- **THEN** `followup` prints the issue number and the missing steps, and `--resume` with the same arguments completes them without creating another issue

#### Scenario: Disagreeing resume
- **WHEN** `--resume` finds the issue already attached to a different parent
- **THEN** it reports the disagreement and writes nothing

### Requirement: The project is enabled by a repository variable and named only in settings
The committed `.github/project-flow.json` SHALL hold only names:
- the Status field name;
- the role → option-name map (`backlog`, `ready`, `in_progress`, `in_review`, `blocked`, `done`);
- the order field name;
- the blocked label name;
- optionally, the name of the Actions environment that holds the variable and the secret.

It MUST NOT hold any project id, project number, owner, field id or option id. The served project SHALL be named only by the variable `PROJECT_URL`, and the write token only by the secret `PROJECT_TOKEN`, both in the environment the config names or, when it names none, at the repository level. While `PROJECT_URL` is unset or names a different project, the Action MUST be a successful no-op, and `sync`, `next`, `start` and `followup` MUST refuse that project with a named reason. If the variable cannot be read, the script MUST report it with the fix and MUST NOT assume a value.

#### Scenario: Not enabled
- **WHEN** the agent runs `next` for a project URL that differs from `PROJECT_URL`
- **THEN** `next` refuses and names the enable step

#### Scenario: Fork without setup
- **WHEN** the workflow runs in a repository with no `PROJECT_URL` or `PROJECT_TOKEN`
- **THEN** it exits successfully with a notice and writes nothing

#### Scenario: Identifier committed
- **WHEN** `.github/project-flow.json` contains a project number, an owner or a node id
- **THEN** `check` fails

#### Scenario: Environment-scoped settings
- **WHEN** `.github/project-flow.json` names an environment
- **THEN** the agent commands read `PROJECT_URL` from that environment, and the workflow job declares the same environment

#### Scenario: No environment configured
- **WHEN** `.github/project-flow.json` names no environment
- **THEN** the agent commands read the repository variable `PROJECT_URL`

### Requirement: The setup check separates facts from heuristics
`project_flow.py check` SHALL validate `.github/project-flow.json` without network access. Given a project URL, it SHALL also report two kinds of findings.

**Facts**, which must be clean before enabling:
- the built-in project workflow *Item closed* when it is missing or disabled, read from the project's workflows (enabling it is possible only in the project's web UI);
- a missing order field or a missing configured Status option;
- open items without an order value;
- In Progress items without an assignee;
- Blocked items without the blocked label.

**Heuristic warnings**:
- the *Auto-add to project* workflow, when it is missing or disabled, as an advisory hint only (issue cards can also be added manually);
- open items whose body or comments mention a dependency ("blocked by", "depends on", "after #") without a matching native link.

The output MUST state that a clean warning list does not prove that dependencies were converted, and that setting `PROJECT_URL` is the user's attestation that they were.

#### Scenario: Offline config check
- **WHEN** `check` runs in CI without a project URL
- **THEN** it validates only the config file and uses no network

#### Scenario: Auto-add missing is not a fact
- **WHEN** *Item closed* is enabled and no *Auto-add to project* workflow exists or it is disabled
- **THEN** `check` lists one advisory warning, reports no setup fact and exits 0

#### Scenario: Unconverted prose dependency
- **WHEN** an open issue says "Depends on #12" and has no native link to it
- **THEN** `check` lists it as a warning, not as a fact

### Requirement: The Action reconciles on every event and runs no PR code
`.github/workflows/project-flow.yml` SHALL trigger on:
- `issues` (closed, reopened, labeled, unlabeled, assigned, unassigned);
- `pull_request_target` (opened, reopened, ready_for_review, converted_to_draft, closed);
- a daily `schedule`;
- `workflow_dispatch`.

In a public repository the pull request events run only where an Actions event policy allows `pull_request_target`, and the setup guide SHALL name that policy. It MUST NOT trigger on `edited`. Every run SHALL execute the same `sync` and write a job summary. It MUST use a single concurrency group with `cancel-in-progress: false`. It MUST check out only the base branch, MUST NOT execute code from the pull request, MUST pass the token only through the secret, and MUST NOT be a required check. Its job SHALL declare, by plain name, the environment `.github/project-flow.json` names, when it names one. The workflow SHALL be owned by the `tooling` node in `.github/components.json`. It MUST be added to the workflow contract suites and the scanner scopes in the same commit, with a negative test that fails if the workflow checks out the PR head or adds an `edited` trigger.

#### Scenario: Fork PR
- **WHEN** a pull request from a fork triggers the workflow
- **THEN** the job runs the base branch's script against PR metadata only

#### Scenario: Body edit
- **WHEN** a PR body is edited to add a closing keyword
- **THEN** no run starts for the edit, and the next event or the daily run reconciles it

### Requirement: The script is deterministic and testable offline
`scripts/project_flow.py` SHALL use only the Python standard library and `gh`, run as `python3 -I`, and reach GitHub through one injectable transport. Every writing subcommand MUST support `--dry-run`, print each write it makes or would make, and exit non-zero with a named reason on every stop case. The tests under `scripts/tests/` MUST run from recorded fixtures with no network, MUST NOT sleep or read the wall clock, and SHALL run in `./gradlew scriptsTest`.

#### Scenario: Dry run
- **WHEN** `sync --dry-run` runs on a board needing two changes
- **THEN** it prints both writes and makes none

#### Scenario: Offline tests
- **WHEN** `./gradlew scriptsTest` runs with the network disabled
- **THEN** the `project_flow` tests pass

### Requirement: A generic setup guide exists
`docs/github-project-setup.md` SHALL describe, for any project and without naming a concrete one:
- the fields and Status options the flow needs and how to map their names;
- the built-in workflows to enable (*Item closed*, *Auto-add to project*);
- the variable `PROJECT_URL`, the secret `PROJECT_TOKEN` and its minimum verified permissions, and the optional environment that holds them;
- the conventions: assignment means started, Blocked is the label, order is a field and not card position, pre-review PRs are drafts, and milestones are feature-level and set during planning;
- converting prose dependencies to native links;
- the ordered enable steps, ending with setting `PROJECT_URL`;
- the rollback: unset `PROJECT_URL`.

#### Scenario: New project
- **WHEN** a contributor sets up a new project by following only the guide and the config file
- **THEN** no code change is needed and `check` reports no fact

### Requirement: The flow is verified end to end on a scratch project
Before the implementation PR leaves draft, the flow SHALL be run end to end in a scratch repository and a scratch project under the user's account, with the workflow, config and script copied there. The run MUST cover:
- Backlog ↔ Ready on blocker changes;
- draft → ready → In Review and back;
- a merge closing the issue and the item becoming Done;
- the blocked label;
- a claim by `start`;
- the milestone copy;
- a follow-up via `followup`;
- `next` on a tie, a missing order value and an empty Ready set.

The run MUST NOT touch the real board or this repository's issues before merge, and the PR MUST state what ran and where.

#### Scenario: Verification record
- **WHEN** the PR is marked ready for review
- **THEN** its description names the scratch repository and project and lists each covered transition with its outcome
