## MODIFIED Requirements

### Requirement: Closeout starts when finalize confirms an open PR
`.agents/rules/repo-superspec-closeout.md` SHALL start the closeout when the last implementation commit is pushed and finalize has confirmed an open pull request, whether the work is tracked or not, and the agent MUST then stay in monitoring mode.

A spec pre-review pull request, when one is opened before finalize, MUST be opened as a draft.

The canonical finalize instruction of the superspec schema:
- MUST create the pull request when the feature branch has none, without asking the user and without a skip choice, and MUST reuse an existing pull request;
- MUST mark a draft pull request ready for review;
- MUST confirm the pull request is open and not a draft (`gh pr view`);
- MUST write and commit `finalize.md` on the feature branch with the true `pr-created` or `pr-updated` outcome, only after that confirmation and before the closeout handoff;
- for tracked work, MUST record the pull request in `tracker.md` (committed and pushed as part of the PR), MUST include a closing keyword for the tracked issue in the PR body, and MUST confirm, after a `project_flow.py sync` that exits 0 and prints `sync: complete`, that the item is In Review (one further run is allowed for the lag in GitHub's indexing of the closing keyword). It MUST NOT write that Status itself. If the item is not In Review, it reports the derived Status and the reason, and the closeout still starts. After a `sync: partial` run or a non-zero exit it MUST report that it could not confirm, with the summary line, assert no Status, and the closeout still starts;
- hands off to the closeout rule.

The outcomes `merge-locally`, `kept-as-is` and `discarded` MUST remain available only when the user explicitly chooses a non-canonical flow, and MUST NOT be selected merely because the feature branch has no pull request. A pull request that cannot be created or confirmed MUST stop finalize: the agent MUST report it and MUST NOT record an outcome or start the closeout. The steps that touch the board, the issue or `tracker.md` SHALL apply only to tracked work.

`templates/finalize.md` MUST state the next step:
- for `pr-created` and `pr-updated`: monitor to green, sync and archive once, review the docs, push, monitor to merge and complete the applicable follow-ups;
- for `merge-locally`: the local-merge requirement below.

The order of the closeout MUST be: babysit to green, sync and archive once, review the docs, push, babysit again, answer comments and monitor to merge, then complete the applicable follow-ups (for tracked work, the post-merge check). Before acting the agent MUST read the *Closeout* and *Monitoring* sections of `docs/superspec-workflow.md` as the rule instructs.

#### Scenario: No pull request exists
- **WHEN** finalize completes implementation on a feature branch that has no pull request
- **THEN** finalize creates the pull request without asking, confirms it is open and ready for review, and the closeout starts

#### Scenario: Existing pre-review pull request
- **WHEN** an open draft spec pre-review pull request already exists for the feature branch
- **THEN** finalize reuses it, marks it ready for review and creates no duplicate

#### Scenario: Escape-hatch outcomes
- **WHEN** the user has not explicitly chosen a non-canonical flow
- **THEN** finalize never records `merge-locally`, `kept-as-is` or `discarded`, and a missing pull request alone selects none of them

#### Scenario: User explicitly chooses a non-canonical flow
- **WHEN** the user explicitly chooses `merge-locally`, `kept-as-is` or `discarded`
- **THEN** finalize records that outcome and follows its next-step bullet, and no canonical pull request is required for it

#### Scenario: Pull request creation or confirmation fails
- **WHEN** the pull request cannot be created, or `gh pr view` does not report it open and ready for review
- **THEN** finalize reports the failure, records no outcome, and does not start the closeout

#### Scenario: Untracked work
- **WHEN** finalize confirms an open PR for work with no tracker reference
- **THEN** the closeout monitors, syncs, archives and reviews the docs, and performs no board or issue write

#### Scenario: Tracked work
- **WHEN** finalize confirms an open, ready-for-review PR with a closing keyword for tracked work, and the issue is open and has no blocked label
- **THEN** the PR is recorded in `tracker.md`, the item is confirmed In Review without the agent writing it, and the closeout starts

#### Scenario: Partial sync at finalize
- **WHEN** the `sync` run after marking the PR ready prints `sync: partial (...)`
- **THEN** finalize reports that it could not confirm the Status, quotes the summary line, writes and asserts no Status, and the closeout still starts

#### Scenario: Derived status is not In Review
- **WHEN** after the `sync` the item is derived as something other than In Review, for example Blocked because of the label
- **THEN** finalize reports the derived Status and its reason, writes no Status, and the closeout still starts

#### Scenario: Finalize template
- **WHEN** `templates/finalize.md` is read for `pr-updated`
- **THEN** its next step lists monitor to green, sync and archive once, review the docs, push, then monitor to merge and complete the applicable follow-ups

### Requirement: A local merge closeout asks before pushing to the default branch
Finalize outcome `merge-locally` remains available only when the user explicitly chooses a non-canonical flow, never merely because no pull request exists, tracked or not. For it, the agent SHALL run the same sync, archive (once) and doc review on the default branch after the local merge, and MUST ask the user before any push to the default branch.

After the user agrees, it SHALL commit and push the sync, archive and doc fixes. For tracked work, only after that push succeeds, it SHALL close the issue with a comment naming the pushed commit and then run `project_flow.py sync`, so that Done is derived.

If the push is rejected (for example by branch protection), the agent MUST report it, MUST NOT retry or bypass the protection, and MUST offer a follow-up pull request instead. If the user declines the push, the agent MUST leave the issue open and the claim in place, and offer the same follow-up pull request.

#### Scenario: Local merge, untracked work
- **WHEN** the user explicitly chooses `merge-locally` for untracked work
- **THEN** sync, archive and doc review run and the agent asks before pushing to the default branch

#### Scenario: Missing pull request is not a reason to skip the PR
- **WHEN** the feature branch has no pull request and the user did not choose any non-canonical flow
- **THEN** finalize creates the pull request and records `pr-created` or `pr-updated`, never `merge-locally`, `kept-as-is` or `discarded`

#### Scenario: Push rejected
- **WHEN** the default-branch push is rejected
- **THEN** the agent reports it, does not retry or bypass, offers a follow-up PR, the issue stays open and the item stays In Progress; a follow-up PR then goes through the normal closeout, with sync, archive and doc review counted as already done

#### Scenario: User declines the push
- **WHEN** the user declines the default-branch push
- **THEN** the issue stays open, the item stays In Progress and a follow-up PR is offered

#### Scenario: Local merge, tracked work
- **WHEN** the default-branch push succeeds
- **THEN** the issue is closed and the next `sync` derives Done

### Requirement: The agent monitors to merge and never merges on its own
After the last push the agent SHALL stay in monitoring mode and keep waiting until the PR is merged or closed, or the user tells it to stop. While waiting it watches:
- review, approval and comments;
- new check results;
- the repository's merge requirements.

It SHALL meet those requirements, for example by updating the branch from the default branch when the repository requires it, and after such a push MUST return to babysitting. The agent MUST NOT merge the PR unless the user asks.

It MUST prefer an event-driven PR watch that ends the turn and wakes it on PR events. Without one it MUST poll at a coarse interval with backoff, reading in one pass the PR's lifecycle state, merge state, head commit, check results, reviews, review decision and comments, and MUST report only state changes. A merge blocker it cannot meet MUST be reported to the user rather than waited on silently. It MUST NOT sleep in a foreground loop and MUST NOT block the user from talking to it. On entering monitoring mode it MUST say what it is waiting for and that the user can tell it to stop.

A user "stop" MUST be handled like a session end. The agent reports the state:
- archived or not;
- open comments;
- for tracked work, that the item's Status is derived automatically from the facts (the `github-project-automation` rules): typically In Review while the PR is open and ready, and Done once the merge closes the issue.

It also reports what stays unfinished and needs a person or a later session: sync and archive if not done, the doc review, and the post-merge check. No later intake completes these steps by itself.

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
- **THEN** it reports the state, says the item's Status follows the facts automatically, and lists the unfinished closeout steps without promising that a later intake completes them

#### Scenario: Merge request
- **WHEN** the user does not ask the agent to merge
- **THEN** the agent never merges the PR

#### Scenario: Polling
- **WHEN** no event-driven watch is available
- **THEN** the agent polls at a coarse interval with backoff and reports only changes

### Requirement: After a merge the applicable follow-ups are completed, Done last
After the implementation PR is merged, the agent SHALL complete the applicable follow-up steps. For tracked work it SHALL run the post-merge check:
- **Issue still open:** the merge should have closed it through the closing keyword. If it did not, the agent closes it with a comment linking the PR and reports that the automation missed it.
- **Done last:** the agent runs `project_flow.py sync` and confirms the item is Done only after a run that exits 0 and prints `sync: complete`. If the item is still not Done, it reports that; after `sync: partial` or a non-zero exit it reports that it could not confirm.
- **Report:** after a `sync: complete` run, the agent reports the items the merge made Ready (the `ready:` lines that are in the merged issue's blocking list), and every follow-up issue the change filed; after a partial run it says the Ready report is incomplete.

If the PR merged before sync, archive and doc review were completed, the agent MUST report it, and the user decides how to proceed, for example with a follow-up PR for the sync and archive. The item may already be Done, because Done follows the closed issue. A PR closed without merging MUST be reported, and the issue MUST NOT be closed.

#### Scenario: Normal merge
- **WHEN** the PR is merged after sync, archive and doc review
- **THEN** the issue is closed by its closing keyword, the item is Done, and the agent reports the newly Ready items

#### Scenario: Automation missed the close
- **WHEN** the merged PR's issue is still open
- **THEN** the agent closes it with a comment linking the PR, runs `sync`, and reports the miss

#### Scenario: Issue close fails
- **WHEN** closing the issue fails
- **THEN** the agent reports it, and the item is not Done until the issue is closed

#### Scenario: Merged by someone else before closeout
- **WHEN** the PR is merged before sync and archive ran
- **THEN** the agent reports it and asks the user how to complete the sync and archive

#### Scenario: Closed without merging
- **WHEN** the PR is closed unmerged
- **THEN** the agent reports it, the issue stays open, and the item's Status is re-derived (In Progress when the claim remains, no other ready closing PR is open and there is no blocked label)
