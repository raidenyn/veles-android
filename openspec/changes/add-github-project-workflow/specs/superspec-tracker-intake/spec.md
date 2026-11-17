## MODIFIED Requirements

### Requirement: Intake applies only to tracked work and names no project
`.agents/rules/repo-tracker-intake.md` SHALL apply when the user names a GitHub Project URL, an issue URL or an issue number as the source of work. Without such a reference nothing MUST be treated as tracked and the agent MUST NOT write to any board or issue. A project named only as an example or as background MUST NOT make the work tracked.

No rule, skill, the superspec schema, `openspec/config.yaml`, `.github/project-flow.json` or `docs/superspec-workflow.md` SHALL name a concrete project, project number, issue number, RFC path or field identifier; the rules MUST work for any project and board.

Before performing intake the agent MUST read the *Next task* and *tracker.md* sections of `docs/superspec-workflow.md` as the rule instructs. It MUST then run the intake through the `github-project` skill, which uses `scripts/project_flow.py` as the `github-project-automation` capability specifies.

#### Scenario: No reference
- **WHEN** the user starts a change without naming a project or issue
- **THEN** no board or issue write happens

#### Scenario: Project named as an example
- **WHEN** the user mentions a project only as an example of what to build
- **THEN** the work is not tracked, and no `tracker.md`, claim or board write happens

#### Scenario: A project is named in a rule
- **WHEN** a rule, the skill, the schema, `openspec/config.yaml` or `.github/project-flow.json` contains a project number, issue number or project URL
- **THEN** review or the plan's verification fails and the identifier is removed

#### Scenario: Pickup request
- **WHEN** the user says "pick up the next task from `<project view URL>`"
- **THEN** the agent runs `project_flow.py next` on that project

### Requirement: The chosen task is checked, announced and read in full
Before creating the change the agent SHALL tell the user which task was chosen and why: the lowest order value among the Ready, unassigned items, or the task the user named.

When the user names a task that is not Ready, the agent MUST say which state it is in and ask before claiming it. Not Ready means Backlog (with its open blockers listed), Blocked, In Review, Done, or assigned. A task that is In Progress and recorded in the `tracker.md` of an active change of this repository MUST be treated as a resume of that change: no claim is repeated and no conflict is reported.

The agent SHALL then read the issue and follow its links (RFC section, pull requests, linked documents, specs, parent and sub-issues) until the context is complete, and MUST write the substantive findings into the brainstorm.

#### Scenario: Unmet dependency
- **WHEN** the user names a task that still has an open native blocker
- **THEN** the agent lists the blocker and asks before claiming it

#### Scenario: Resume
- **WHEN** the task is In Progress and an active change's `tracker.md` records it
- **THEN** the agent continues that change without reporting a conflict and without a new claim

#### Scenario: Linked context
- **WHEN** the issue links an RFC section and a pull request
- **THEN** the agent reads both before the brainstorm and records what bears on the design

### Requirement: Issue-only intake resolves the repository and board
When the user names an issue instead of a board, the agent SHALL:
1. resolve the repository explicitly: from the URL, or from the checkout's remote for a bare number, asking when it is ambiguous;
2. list the projects that contain the issue;
3. use the only one, and ask when there are several.

With no project, or with a project that is not the one enabled by `PROJECT_URL`, the work MUST be treated as untracked from then on:
- the agent uses the issue for context only;
- it writes no `tracker.md`;
- it makes no claim;
- it writes nothing to any board or issue, at intake or at closeout.

The agent MAY put a non-closing reference to the issue in the PR body, for example "Refs #12".

#### Scenario: Several projects
- **WHEN** an issue belongs to two projects
- **THEN** the agent asks which board to use

#### Scenario: No project
- **WHEN** an issue belongs to no project
- **THEN** the agent reads it for context and performs no board write

#### Scenario: Project not enabled
- **WHEN** the issue's only project is not the one named by `PROJECT_URL`
- **THEN** the agent reports that the project is not enabled, uses the issue for context, writes no `tracker.md`, makes no claim, and the closeout performs no board or issue write

### Requirement: tracker.md records the tracked association
For tracked work the agent SHALL create `openspec/changes/<name>/tracker.md` right after the change is created and before the claim. It records:
- the repository;
- the project;
- the issue;
- the source documents;
- the creation date;
- later, the implementation PR.

`tracker.md` MUST NOT be a planning artifact, MUST NOT be reviewed and MUST NOT be part of any approval hash. The implementation PR MUST be added to it after finalize confirms an open PR, and committed and pushed as part of that PR before the merge.

#### Scenario: Interrupted intake
- **WHEN** a session ends after the change is created and before the claim
- **THEN** `tracker.md` already exists and a later session resumes from it

#### Scenario: PR recorded after verification
- **WHEN** finalize adds the PR to `tracker.md`
- **THEN** no review record becomes stale

#### Scenario: Validation tolerates the file
- **WHEN** `openspec validate --all` runs with `tracker.md` and `reviews/` present
- **THEN** validation reports no error caused by them

## ADDED Requirements

### Requirement: The next task is selected by order value
For a pickup request without a named task, the agent SHALL use the task chosen by `project_flow.py next`. When `next` stops, the agent MUST report its named reason and MUST NOT pick by any other order. The reasons are: the project is not enabled, nothing is Ready, a tie, a missing order value, or an incomplete read.
- For `missing-order`, the agent MUST ask whether to continue with `next --skip-unordered` or to set the missing values first.
- For every other reason, it MUST ask the user to name the task.

#### Scenario: Selected
- **WHEN** `next` selects an item
- **THEN** the agent announces it with its order value and proceeds to read it

#### Scenario: Stopped
- **WHEN** `next` stops because two items share the lowest order value
- **THEN** the agent reports both and asks the user which to take

### Requirement: The agent claims the task; every other status is derived
After writing `tracker.md`, the agent SHALL claim the task with `project_flow.py start`, which assigns the issue and sets In Progress. Apart from the claim, the agent MUST NOT write the Status of a tracked item: Ready, Backlog, In Review, Blocked and Done are derived as the `github-project-automation` capability specifies. To mark work Blocked the agent MUST add the blocked label, and to release a claim it MUST remove its assignment.

A missing `gh` scope MUST be reported with the exact command to fix it and not worked around. For the finalize outcomes:
- `discarded`: the agent MUST report and ask whether to release the claim or add the blocked label;
- `kept-as-is`: it MUST keep the claim, so the item stays In Progress;
- `merge-locally`: the closeout capability's local-merge requirement applies.

#### Scenario: Change created
- **WHEN** the change for a tracked task is created
- **THEN** `tracker.md` is written first, then `start` assigns the issue and the item is In Progress

#### Scenario: Pre-review PR
- **WHEN** a planning pre-review PR exists before finalize
- **THEN** it is a draft and the item stays In Progress

#### Scenario: Agent wants to mark work blocked
- **WHEN** the agent is stuck on something outside the board
- **THEN** it adds the blocked label and does not write the Status directly

#### Scenario: Discarded
- **WHEN** finalize ends with `discarded`
- **THEN** the agent asks whether to release the claim or add the blocked label

### Requirement: Follow-up work is finished or filed
During a tracked change the agent SHALL first finish the work that no open decision blocks. Work that changes the approved planning files no more than the review gate's non-triggering threshold, and needs no new brainstorm, SHALL be done in the same change. Larger work MUST be filed with `project_flow.py followup`, and the PR body MUST list every follow-up issue it created. When the size or scope is unclear, the agent MUST ask the user.

#### Scenario: Small finding
- **WHEN** the agent finds a missing edge-case test that changes no planning file
- **THEN** it adds the test in the same change

#### Scenario: Large finding
- **WHEN** the agent finds work that would need its own brainstorm
- **THEN** it files a follow-up issue with `followup` and lists it in the PR body

#### Scenario: Unclear
- **WHEN** it is unclear whether a finding fits the change
- **THEN** the agent asks the user

### Requirement: The procedure is delivered as rules, a doc and a skill
The tracked-work procedure SHALL be split as follows.

**Normative rules** live in the *Next task* and *tracker.md* sections of `docs/superspec-workflow.md`, and those headings MUST remain:
- the scope;
- the selection rule;
- the claim;
- the follow-up rule;
- `tracker.md`.

**Commands** live in the skill `.agents/skills/github-project/SKILL.md`, with its `.claude/skills/github-project` symlink:
- the `project_flow.py` invocations;
- the PR-body template with the closing keyword and the follow-up list;
- the `followup` and `--resume` usage;
- the post-merge check.

The skill MUST cite the doc sections and MUST NOT restate their rules. `.agents/rules/repo-tracker-intake.md` MUST keep its opening instruction to read *Next task* and *tracker.md*, and MUST tell the agent to run the steps through the skill.

#### Scenario: Skill present and linked
- **WHEN** the agent-file check runs
- **THEN** `.agents/skills/github-project/` has its `.claude/skills/github-project` link, and `openspec update` leaves it unchanged

#### Scenario: Rule opening kept
- **WHEN** the plan's verification greps `repo-tracker-intake.md` for its opening sentence
- **THEN** it still sends the agent to *Next task* and *tracker.md* of `docs/superspec-workflow.md`

## REMOVED Requirements

### Requirement: The next task is the successor of the last completed one
**Reason**: Board position and view settings cannot be read reliably, and the successor rule needed many stop cases. Selection now uses an order field and a derived Ready status.

**Migration**: Use "The next task is selected by order value". Give every open item an order value and enable the project as the setup guide describes; reorder by editing the order field instead of dragging cards.

### Requirement: Statuses follow the superspec transitions
**Reason**: Statuses are now derived from facts by the shared script, and the agent only claims.

**Migration**: Use "The agent claims the task; every other status is derived" and the `github-project-automation` capability. In Review now comes from marking the PR ready for review; Blocked comes from the blocked label.

### Requirement: Reconcile marks merged work Done at the next intake
**Reason**: Done is derived from the closed issue by the built-in workflow and by every `sync`, so there is nothing left to reconcile from archived `tracker.md` files.

**Migration**: `next` runs `sync` before selecting, which repairs any item whose closed issue is not yet Done. Items from the old flow that are In Review with a merged PR become Done once their issue is closed.
