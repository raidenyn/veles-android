## MODIFIED Requirements

### Requirement: Closeout starts when finalize confirms an open PR
`.agents/rules/repo-superspec-closeout.md` SHALL start the closeout when the last implementation commit is pushed and finalize has confirmed an open pull request, whether the work is tracked or not, and the agent MUST then stay in monitoring mode. The steps that touch the board, the issue or `tracker.md` SHALL apply only to tracked work. The finalize instruction of the superspec schema MUST confirm the open PR (`gh pr view`), record it in `tracker.md` for tracked work (committed and pushed as part of the PR), set In Review as the tracker capability specifies, and hand off to the closeout rule. `templates/finalize.md` MUST state the next step for `pr-created` and `pr-updated` as: monitor to green, sync and archive once, review the docs, push, monitor to merge and complete the applicable follow-ups; and for `merge-locally`: the local-merge requirement below. The order of the closeout MUST be: babysit to green, sync and archive once, review the docs, push, babysit again, answer comments and monitor to merge, then complete the applicable follow-ups (the issue and the board for tracked work). Before acting the agent MUST read the *Closeout* and *Monitoring* sections of `docs/superspec-workflow.md` as the rule instructs.

#### Scenario: Untracked work
- **WHEN** finalize confirms an open PR for work with no tracker reference
- **THEN** the closeout monitors, syncs, archives and reviews the docs, and performs no board or issue write

#### Scenario: Tracked work
- **WHEN** finalize confirms an open PR for tracked work
- **THEN** the PR is recorded in `tracker.md`, the card is In Review and the closeout starts

#### Scenario: Finalize template
- **WHEN** `templates/finalize.md` is read for `pr-updated`
- **THEN** its next step lists monitor to green, sync and archive once, review the docs, push, then monitor to merge and complete the applicable follow-ups

### Requirement: The agent monitors to merge and never merges on its own
After the last push the agent SHALL stay in monitoring mode and keep waiting for review, approval, comments, new check results and the repository's merge requirements until the PR is merged or closed or the user tells it to stop. It SHALL meet those requirements, for example by updating the branch from the default branch when the repository requires it, and after such a push MUST return to babysitting. The agent MUST NOT merge the PR unless the user asks. It MUST prefer an event-driven PR watch that ends the turn and wakes it on PR events; without one it MUST poll at a coarse interval with backoff, reading the PR's lifecycle state, merge state, head commit, check results, reviews, review decision and comments in one pass, and MUST report only state changes. A merge blocker it cannot meet MUST be reported to the user rather than waited on silently. It MUST NOT sleep in a foreground loop and MUST NOT block the user from talking to it. On entering monitoring mode it MUST say what it is waiting for and that the user can tell it to stop. A user "stop" MUST be handled like a session end: the agent reports the state (archived or not, open comments, the card stays In Review) and that the next intake's reconcile step will finish the card if the change was archived and the PR is merged, and otherwise report the unfinished closeout to the user.

#### Scenario: Branch update required
- **WHEN** the repository requires the PR branch to be updated from the default branch before merging
- **THEN** the agent updates the branch, returns to babysitting, and keeps monitoring until the PR is merged

#### Scenario: Blocked merge it cannot meet
- **WHEN** the PR is blocked by a merge requirement the agent cannot satisfy
- **THEN** it reports the blocker to the user and does not wait on it silently

#### Scenario: Nobody has reviewed yet
- **WHEN** checks are green and the PR has no review
- **THEN** the agent keeps waiting and does not stop on its own

#### Scenario: User asks to stop
- **WHEN** the user tells the agent to stop waiting
- **THEN** it reports the state and says the next intake will reconcile the card if the change was archived and the PR is merged, and otherwise report the unfinished closeout

#### Scenario: Merge request
- **WHEN** the user does not ask the agent to merge
- **THEN** the agent never merges the PR

#### Scenario: Polling
- **WHEN** no event-driven watch is available
- **THEN** the agent polls at a coarse interval with backoff and reports only changes

### Requirement: After a merge the applicable follow-ups are completed, Done last
After the implementation PR is merged the agent SHALL complete the applicable follow-up steps. For tracked work, when sync, archive and doc review were completed, it SHALL first make sure the issue is closed (closing it with a comment linking the PR if the merge did not) and then set the project item's Status to Done last, so that the item stays In Review until both writes succeed. A PR merged before sync, archive and doc review were completed, or closed without merging, MUST be reported to the user, nothing MUST be marked Done and the user decides how to proceed (for example a follow-up PR for the sync and archive).

#### Scenario: Normal merge
- **WHEN** the PR is merged after sync, archive and doc review
- **THEN** the issue is closed and the card is set to Done last

#### Scenario: Issue close fails
- **WHEN** closing the issue fails
- **THEN** the card stays In Review so a later reconcile retries

#### Scenario: Merged by someone else before closeout
- **WHEN** the PR is merged before sync and archive ran
- **THEN** the agent reports it and marks nothing Done

#### Scenario: Closed without merging
- **WHEN** the PR is closed unmerged
- **THEN** the agent reports it and marks nothing Done

## RENAMED Requirements

- FROM: `### Requirement: The agent keeps waiting and never merges`
- TO: `### Requirement: The agent monitors to merge and never merges on its own`

- FROM: `### Requirement: Done is set only after a completed closeout and a merge`
- TO: `### Requirement: After a merge the applicable follow-ups are completed, Done last`
