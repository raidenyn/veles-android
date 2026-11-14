## Purpose
This capability defines how a task tracked on a GitHub Project is picked up and followed: selecting the next task after the last completed one, gathering its context, recording the association in `tracker.md`, moving the card to In Progress and In Review, and reconciling Done at the next intake, without naming any concrete project.

## ADDED Requirements

### Requirement: Intake applies only to tracked work and names no project
`.agents/rules/repo-tracker-intake.md` SHALL apply when the user names a GitHub Project URL, an issue URL or an issue number as the source of work. Without such a reference nothing MUST be treated as tracked and the agent MUST NOT write to any board or issue. No rule, the superspec schema, `openspec/config.yaml` or `docs/superspec-workflow.md` SHALL name a concrete project, project number, issue number, RFC path or field identifier; the rules MUST work for any project and board. Before performing intake the agent MUST read the *Next task* and *tracker.md* sections of `docs/superspec-workflow.md` as the rule instructs.

#### Scenario: No reference
- **WHEN** the user starts a change without naming a project or issue
- **THEN** no board or issue write happens

#### Scenario: A project is named in a rule
- **WHEN** a rule, the schema or `openspec/config.yaml` contains a project number, issue number or project URL
- **THEN** review or the plan's verification fails and the identifier is removed

#### Scenario: Pickup request
- **WHEN** the user says "pick up the next task from `<project view URL>`"
- **THEN** the agent runs the next-task selection on that project

### Requirement: The next task is the successor of the last completed one
The agent SHALL read every item of the project, paging until no page remains, and treat a partial read as a failure. The order MUST come from the view's sort field when the URL names a view with sort fields, and MUST be the project's item order (the default position order of the project's items) for a view with no sort or a project URL with no view. A view with several sort keys, with grouping, or whose order cannot be recovered MUST make the agent ask the user to name the task and never substitute another order. An item is **completed** when its project Status option is the one named Done or its underlying issue is closed, judged over all items and ignoring the view's filter; a draft or pull-request item has no underlying issue and is completed only by its Status. Because an item is completed when either signal holds, only the last completed item can make the signals matter: if they disagree for it (Status Done with an open issue, or a closed issue that is not Done) the agent MUST report it and ask, and a disagreement for any other item MUST NOT block pickup. The task is the first item after the last completed item in the order; when the view's filter is a single simple field match the task is the first such item that matches it (and when no later item matches the agent MUST stop and report), and any other filter MUST make the agent ask. With no completed item, a tie in the sort key that makes the last completed item or the task ambiguous, or the last completed item being the last item, the agent MUST stop and report to the user; a tie elsewhere in the order MUST NOT block pickup.

#### Scenario: Hand-ordered board
- **WHEN** the URL names a view with no sort field and the project's item order lists the completed items and then the next task
- **THEN** the agent selects that next task and announces it

#### Scenario: Done items hidden by the view
- **WHEN** the view's filter hides Done items
- **THEN** completion is still judged over all project items

#### Scenario: Several sort keys or grouping
- **WHEN** the view sorts by two fields or groups items
- **THEN** the agent asks the user to name the task

#### Scenario: Filter narrows the successor
- **WHEN** the view's filter is one simple field match and the next item in order does not match it
- **THEN** the task is the first later item that matches

#### Scenario: Nothing matches the filter
- **WHEN** the view's filter is one simple field match and no item after the last completed one matches it
- **THEN** the agent stops and reports

#### Scenario: Tie far from the task
- **WHEN** two items far after the chosen task share a sort value
- **THEN** pickup is not blocked

#### Scenario: Tie at the boundary
- **WHEN** a tie makes the last completed item ambiguous
- **THEN** the agent stops and reports

#### Scenario: Disagreement at the boundary
- **WHEN** the last completed item has Status Done but an open issue
- **THEN** the agent reports it and asks before choosing the next task

#### Scenario: Disagreement on an earlier item
- **WHEN** an item before the last completed item has a closed issue but a Backlog Status
- **THEN** pickup is not blocked by it

#### Scenario: No completed item
- **WHEN** no item is completed
- **THEN** the agent stops and reports instead of choosing the first item

#### Scenario: Partial read
- **WHEN** the items cannot be read to the last page
- **THEN** the agent reports a failure and selects nothing

### Requirement: The chosen task is checked, announced and read in full
Before creating the change the agent SHALL verify that the task's declared dependencies are completed and that it is not already In Progress, and MUST tell the user which task was chosen, which completed task precedes it and why. A task that is In Progress and recorded in the `tracker.md` of an active change of this repository MUST be treated as a resume of that change, not a conflict. Any other unmet dependency or In Progress state MUST make the agent stop and ask. The agent SHALL then read the issue and follow its links (RFC section, pull requests, linked documents and specs) until the context is complete, and MUST write the substantive findings into the brainstorm.

#### Scenario: Unmet dependency
- **WHEN** a declared dependency of the chosen task is not completed
- **THEN** the agent stops and asks the user

#### Scenario: Resume
- **WHEN** the task is In Progress and an active change's `tracker.md` records it
- **THEN** the agent continues that change without reporting a conflict

#### Scenario: Linked context
- **WHEN** the issue links an RFC section and a pull request
- **THEN** the agent reads both before the brainstorm and records what bears on the design

### Requirement: Issue-only intake resolves the repository and board
When the user names an issue instead of a board the agent SHALL resolve the repository explicitly (from the URL, or from the checkout's remote for a bare number, asking when ambiguous), list the projects that contain the issue, use the only one, ask when there are several, and with none use the issue for context only and MUST NOT write to any board.

#### Scenario: Several projects
- **WHEN** an issue belongs to two projects
- **THEN** the agent asks which board to update

#### Scenario: No project
- **WHEN** an issue belongs to no project
- **THEN** the agent reads it for context and performs no board write

### Requirement: tracker.md records the tracked association
For tracked work the agent SHALL create `openspec/changes/<name>/tracker.md` right after the change is created and before the first status write, recording the repository, the project, the issue, the source documents, the creation date and later the implementation PR. `tracker.md` MUST NOT be a planning artifact, MUST NOT be reviewed and MUST NOT be part of any approval hash. The implementation PR MUST be added to it after finalize confirms an open PR, committed and pushed as part of that PR before the merge.

#### Scenario: Interrupted intake
- **WHEN** a session ends after the change is created and before the status write
- **THEN** `tracker.md` already exists and a later session resumes from it

#### Scenario: PR recorded after verification
- **WHEN** finalize adds the PR to `tracker.md`
- **THEN** no review record becomes stale

#### Scenario: Validation tolerates the file
- **WHEN** `openspec validate --all` runs with `tracker.md` and `reviews/` present
- **THEN** validation reports no error caused by them

### Requirement: Statuses follow the superspec transitions
The agent SHALL set the project item's Status to In Progress when the change is created, and to In Review only when finalize has succeeded and an open PR for the branch is confirmed and recorded; a push without a PR, or an earlier planning pre-review PR, MUST leave the card In Progress. Statuses MUST be matched by name on the project's status field, and if the field or an option cannot be found the agent MUST stop and ask. A missing `gh` scope MUST be reported with the exact command to fix it and not worked around. For finalize outcome `merge-locally` the closeout capability's local-merge requirement applies, and the agent MUST close the issue and then set Done only after that requirement's default-branch push succeeds; for `discarded` it MUST report and ask what the status becomes; for `kept-as-is` it MUST leave the card In Progress. Done after a pull request belongs to the closeout capability.

#### Scenario: Change created
- **WHEN** the change for a tracked task is created
- **THEN** the item moves to In Progress after `tracker.md` is written

#### Scenario: Push without a PR
- **WHEN** finalize pushes the branch and no PR exists
- **THEN** the card stays In Progress

#### Scenario: Pre-review PR
- **WHEN** a planning pre-review PR exists before finalize
- **THEN** the card is not In Review until finalize succeeds with a confirmed open PR

#### Scenario: Missing status option
- **WHEN** the project's Status field has no option named In Review
- **THEN** the agent stops and asks

#### Scenario: Local merge
- **WHEN** finalize ends with `merge-locally`
- **THEN** sync, archive and the doc review run on the default branch and are pushed before the issue is closed and the card is set to Done

#### Scenario: Local merge push fails
- **WHEN** the default-branch push of a `merge-locally` closeout fails
- **THEN** the issue stays open and the card is not set to Done

#### Scenario: Discarded
- **WHEN** finalize ends with `discarded`
- **THEN** the agent asks the user what the status becomes

### Requirement: Reconcile marks merged work Done at the next intake
At the start of every intake the agent SHALL, for project items whose Status is In Review and whose implementation PR, recorded in the `tracker.md` of an already archived change (read from the remote default branch after a fetch), is merged, close the issue and then set Done. The step MUST write nothing to the repository, MUST be idempotent and is the only cross-item write authorized. The agent MUST report instead of writing when an item has several recorded PRs, its PR was closed unmerged, its change is not archived, or no archived `tracker.md` is found for an In Review item. A session that finds the issue already closed or the card already Done MUST treat that as success.

#### Scenario: Session ended before the merge
- **WHEN** a previous session ended while the PR was open and the PR is now merged
- **THEN** the next intake closes the issue and sets Done

#### Scenario: Overlap with a monitoring session
- **WHEN** a monitoring session and a new intake both reconcile the same item
- **THEN** both succeed without error

#### Scenario: Closed unmerged
- **WHEN** a recorded PR was closed without merging
- **THEN** the agent reports it and marks nothing Done

#### Scenario: Stale checkout
- **WHEN** the local checkout lacks the archive that the remote default branch has
- **THEN** the agent fetches and reads the remote before deciding
