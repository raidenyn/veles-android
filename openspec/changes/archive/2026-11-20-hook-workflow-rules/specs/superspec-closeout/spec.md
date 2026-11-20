## ADDED Requirements

### Requirement: The closeout rule is hook-delivered and guards the stop
The closeout rule SHALL be the hook-delivered rule `.agents/hooked-rules/repo-superspec-closeout.md`. It MUST be
delivered at the apply, verify, continue and archive skills and at `openspec instructions verify` and
`openspec instructions finalize`, and MUST declare the stop predicate `superspec-finalize-pending`
(capability `hooked-rule-delivery`).

Its body MUST open with this instruction: after the last apply task, run verify and then finalize in the same
turn, without asking, because the owner durably authorizes finalize to push the feature branch, create or update
the pull request and mark it ready; stop only when verify fails, finalize cannot create or confirm the pull
request, a rule requires asking the user (a device or browser is unavailable, a blocker, an ambiguity) or the user
said to stop. Its always-on pointer MUST carry the same
authorization in one line.

`openspec/config.yaml` `operations.apply.guidance` MUST carry the same instruction as its own entry, one sentence
citing `.agents/hooked-rules/repo-superspec-closeout.md`, next to the separate entry that points to the apply stop
(capability `superspec-review-gate`).

#### Scenario: Apply finishes
- **WHEN** an agent finishes the last apply task
- **THEN** the closeout rule and the apply guidance in its context tell it to run verify and then finalize without asking, and the end-to-end run of `hooked-rule-delivery` outcome 7 records whether it did

#### Scenario: Agent stops to ask
- **WHEN** the agent ends its turn after apply to ask whether to push or open the pull request
- **THEN** the stop gate blocks the stop once with the closeout rule and the engine line telling it to finalize now

#### Scenario: A rule requires asking
- **WHEN** a required device is unavailable after apply and the stop gate blocks the agent's stop
- **THEN** the agent says that a rule requires it to ask and ends its turn, and that next stop is not blocked

#### Scenario: Apply guidance
- **WHEN** `openspec instructions apply` is run
- **THEN** its guidance tells the agent to run verify and finalize without asking and names `.agents/hooked-rules/repo-superspec-closeout.md`

## MODIFIED Requirements

### Requirement: Closeout starts when finalize confirms an open PR
`.agents/hooked-rules/repo-superspec-closeout.md` SHALL start the closeout when the last implementation commit is
pushed and finalize has confirmed an open pull request, whether the work is tracked or not, and the agent MUST then
stay in monitoring mode.

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

The order of the closeout MUST be: babysit to green, sync and archive once, review the docs, push, babysit again, answer comments and monitor to merge, then complete the applicable follow-ups (for tracked work, the post-merge check). Before acting the agent MUST read the *Closeout* and *Monitoring* sections of `docs/superspec-workflow.md` as the rule instructs. The schema's references to the closeout rule MUST use its `.agents/hooked-rules/` path.

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
