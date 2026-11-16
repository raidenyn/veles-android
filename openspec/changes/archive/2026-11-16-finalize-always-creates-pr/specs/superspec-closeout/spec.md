## MODIFIED Requirements

### Requirement: Closeout starts when finalize confirms an open PR
`.agents/rules/repo-superspec-closeout.md` SHALL start the closeout when the last implementation commit is pushed and finalize has confirmed an open pull request, whether the work is tracked or not, and the agent MUST then stay in monitoring mode. The canonical finalize instruction of the superspec schema MUST create the pull request when the feature branch has none, without asking the user and without a skip choice, and MUST reuse an existing pull request. It MUST confirm the pull request is open (`gh pr view`), write and commit `finalize.md` on the feature branch with the true `pr-created` or `pr-updated` outcome only after that confirmation and before the closeout handoff, record the pull request in `tracker.md` for tracked work (committed and pushed as part of the PR), set In Review as the tracker capability specifies, and hand off to the closeout rule. The outcomes `merge-locally`, `kept-as-is` and `discarded` MUST remain available only when the user explicitly chooses a non-canonical flow, and MUST NOT be selected merely because the feature branch has no pull request. A pull request that cannot be created or confirmed MUST stop finalize: the agent MUST report it and MUST NOT record an outcome or start the closeout. The steps that touch the board, the issue or `tracker.md` SHALL apply only to tracked work. `templates/finalize.md` MUST state the next step for `pr-created` and `pr-updated` as: monitor to green, sync and archive once, review the docs, push, monitor to merge and complete the applicable follow-ups; and for `merge-locally`: the local-merge requirement below. The order of the closeout MUST be: babysit to green, sync and archive once, review the docs, push, babysit again, answer comments and monitor to merge, then complete the applicable follow-ups (the issue and the board for tracked work). Before acting the agent MUST read the *Closeout* and *Monitoring* sections of `docs/superspec-workflow.md` as the rule instructs.

#### Scenario: No pull request exists
- **WHEN** finalize completes implementation on a feature branch that has no pull request
- **THEN** finalize creates the pull request without asking, confirms it is open, and the closeout starts

#### Scenario: Existing pre-review pull request
- **WHEN** an open spec pre-review pull request already exists for the feature branch
- **THEN** finalize reuses and updates it and creates no duplicate

#### Scenario: Escape-hatch outcomes
- **WHEN** the user has not explicitly chosen a non-canonical flow
- **THEN** finalize never records `merge-locally`, `kept-as-is` or `discarded`, and a missing pull request alone selects none of them

#### Scenario: User explicitly chooses a non-canonical flow
- **WHEN** the user explicitly chooses `merge-locally`, `kept-as-is` or `discarded`
- **THEN** finalize records that outcome and follows its next-step bullet, and no canonical pull request is required for it

#### Scenario: Pull request creation or confirmation fails
- **WHEN** the pull request cannot be created, or `gh pr view` does not report it open
- **THEN** finalize reports the failure, records no outcome, and does not start the closeout

#### Scenario: Untracked work
- **WHEN** finalize confirms an open PR for work with no tracker reference
- **THEN** the closeout monitors, syncs, archives and reviews the docs, and performs no board or issue write

#### Scenario: Tracked work
- **WHEN** finalize confirms an open PR for tracked work
- **THEN** the PR is recorded in `tracker.md`, the card is In Review and the closeout starts

#### Scenario: Finalize template
- **WHEN** `templates/finalize.md` is read for `pr-updated`
- **THEN** its next step lists monitor to green, sync and archive once, review the docs, push, then monitor to merge and complete the applicable follow-ups

### Requirement: A local merge closeout asks before pushing to the default branch
For finalize outcome `merge-locally`, which remains available only when the user explicitly chooses a non-canonical flow and never merely because no pull request exists, tracked or not, the agent SHALL run the same sync, archive (once) and doc review on the default branch after the local merge, and MUST ask the user before any push to the default branch. After the user agrees it SHALL commit and push the sync, archive and doc fixes, and for tracked work close the issue and then set Done only after that push succeeds. If the push is rejected (for example by branch protection) the agent MUST report it, MUST NOT retry or bypass the protection, and MUST offer a follow-up pull request instead; if the user declines the push, the agent MUST leave the issue open and the card In Progress and offer the same follow-up pull request.

#### Scenario: Local merge, untracked work
- **WHEN** the user explicitly chooses `merge-locally` for untracked work
- **THEN** sync, archive and doc review run and the agent asks before pushing to the default branch

#### Scenario: Missing pull request is not a reason to skip the PR
- **WHEN** the feature branch has no pull request and the user did not choose any non-canonical flow
- **THEN** finalize creates the pull request and records `pr-created` or `pr-updated`, never `merge-locally`, `kept-as-is` or `discarded`

#### Scenario: Push rejected
- **WHEN** the default-branch push is rejected
- **THEN** the agent reports it, does not retry or bypass, offers a follow-up PR, the issue stays open and the card stays In Progress; a follow-up PR then goes through the normal closeout (In Review once an open PR is confirmed, Done after the merge), with sync, archive and doc review counted as already done

#### Scenario: User declines the push
- **WHEN** the user declines the default-branch push
- **THEN** the issue stays open, the card stays In Progress and a follow-up PR is offered

#### Scenario: Local merge, tracked work
- **WHEN** the default-branch push succeeds
- **THEN** the issue is closed and then the card is set to Done
