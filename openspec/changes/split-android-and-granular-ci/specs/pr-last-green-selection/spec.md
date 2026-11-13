## ADDED Requirements

### Requirement: First PR run diffs against the merge base
The first `ci.yml` run of a pull request SHALL select nodes by diffing the pull request against its merge base with the base branch, without rename detection, exactly as before this change. A run for which no earlier evidence head exists MUST behave the same way.

#### Scenario: Opened pull request
- **WHEN** a pull request is opened and changes only `web-extension/src/`
- **THEN** `web-extension` is selected and the nodes that do not own that path are skipped

#### Scenario: Missing merge base
- **WHEN** the merge base cannot be computed
- **THEN** every node is selected

### Requirement: Later PR runs select each node against its last green head
For every `ci.yml` run of a pull request after the first, the `changes` job SHALL decide per node. When a green evidence head exists for the node, the node MUST be selected only if a path it owns changed between that evidence head and the pull request head (`github.event.pull_request.head.sha`). The node is the unit: when selected, all of its jobs MUST run.

#### Scenario: Android passed, docs pushed
- **WHEN** `android` was green on the previous pull request head and the next push changes only `docs/ci.md`
- **THEN** `android` is skipped

#### Scenario: Android passed, web extension pushed
- **WHEN** `android` was green on the previous head and the next push changes only `web-extension/src/`
- **THEN** `android` is skipped and `web-extension` is selected

#### Scenario: Android passed, shared Rust pushed
- **WHEN** `android` was green on the previous head and the next push changes `rust/veles-crypto/src/lib.rs`
- **THEN** `android` is selected and all of its jobs run

#### Scenario: Master movement is ignored
- **WHEN** `master` advanced with changes to `android/` after the evidence head and the pull request itself did not touch `android/`
- **THEN** `android` is skipped

### Requirement: Green means one complete run attempt
A node SHALL count as green on an evidence head only when, in a single attempt of a completed `ci.yml` run triggered by the `pull_request` event for that head, every job of the node's expected job set has conclusion `success` and no additional job of that node exists. Skipped, neutral, cancelled, failed, missing and in-progress jobs MUST NOT count, and jobs of different runs or attempts MUST NOT be combined.

#### Scenario: All jobs of one attempt succeeded
- **WHEN** every expected `android / ...` job succeeded in one attempt of the `ci.yml` run for a head
- **THEN** `android` is green on that head

#### Scenario: One job failed
- **WHEN** `android / unit-tests` failed in the run for a head
- **THEN** `android` is not green on that head

#### Scenario: Failed jobs re-run only
- **WHEN** a "re-run failed jobs" attempt did not execute the sibling jobs that had passed in the earlier attempt
- **THEN** the node is not green on that head and runs again on the next relevant push

#### Scenario: Results are not combined across runs
- **WHEN** `android / lint` succeeded in one run for a head and `android / unit-tests` succeeded in a different run for the same head
- **THEN** `android` is not green on that head

#### Scenario: Extra job
- **WHEN** the evidence run contains an `android / ...` job that is not in the node's expected set
- **THEN** `android` is not green on that head

### Requirement: Evidence runs belong to this pull request and pin their attempt
An evidence run SHALL belong to the pull request: its `pull_requests` list contains the pull request number or, when that list is empty (fork pull requests), its head repository and head branch equal those of the pull request. Jobs MUST be read from the attempt endpoint of the run's attempt number, across all pages, and a run MUST be rejected when any returned job reports a different attempt number.

#### Scenario: Attempt changes during lookup
- **WHEN** a job returned for the pinned attempt reports a different attempt number
- **THEN** the run is rejected as evidence

#### Scenario: Run from another pull request
- **WHEN** a run for the same head SHA belongs to a different pull request
- **THEN** it is not used as evidence

#### Scenario: Fork pull request
- **WHEN** the run's `pull_requests` list is empty and its head repository and head branch equal those of the pull request
- **THEN** the run is accepted as evidence

#### Scenario: Paginated jobs and commits
- **WHEN** the pull request has more commits, or a run has more jobs, than fit on one API page
- **THEN** every page is read within the lookup bounds before a node is judged green or not green

### Requirement: Evidence heads must be ancestors of the current head
Candidate evidence heads SHALL be the pull request's commits, newest first. A candidate that is not an ancestor of the current pull request head MUST be skipped individually, the newest usable green candidate MUST be used, and a node MUST be treated as having no usable evidence only when no candidate remains.

#### Scenario: Normal push
- **WHEN** the previous head is an ancestor of the new head
- **THEN** the diff from the previous head to the new head decides selection

#### Scenario: Force-push without a surviving green ancestor
- **WHEN** no green candidate is an ancestor of the new head
- **THEN** the node is evaluated against the merge base like a node without evidence

#### Scenario: Force-push with a surviving green ancestor
- **WHEN** the latest head was rewritten but an older green head is still an ancestor of the new head
- **THEN** that older head is used as the evidence head

#### Scenario: Master merged into the branch
- **WHEN** the branch merges `master` and the merge touches paths owned by a node
- **THEN** that node is selected

### Requirement: Evidence must match the current selection definition
Evidence SHALL be used only when the component map's normalised selection definition at the evidence head equals the current one: the node's own `paths`, `ci-paths`, matching `retired-paths` entries and `jobs`, together with the complete `global`, `ci-global`, `global-overrides`, `retired-paths` and `unowned` sections. Any difference MUST make the evidence incompatible, and the node MUST then run when any of its paths changed against the merge base. A job set in the evidence run that does not exactly equal the node's current expected set MUST NOT count as green.

#### Scenario: Equivalent map
- **WHEN** the map differs from the evidence head's only in key order or whitespace
- **THEN** the evidence stays compatible

#### Scenario: Node inputs expanded
- **WHEN** an edit to `.github/components.json` adds a path that the pull request already changed to the `android` node's `paths`
- **THEN** earlier `android` evidence is incompatible and `android` is selected

#### Scenario: Override removed
- **WHEN** a `global-overrides` entry that excluded `native-host` is removed and a pull request path now selects every node
- **THEN** earlier `native-host` evidence is incompatible

#### Scenario: Override membership changed
- **WHEN** the node list of a `global-overrides` entry changes
- **THEN** the evidence of every node is incompatible

#### Scenario: Job renamed
- **WHEN** a node's job was renamed after the evidence run
- **THEN** the evidence job set differs from the current expected set and the node is not green

### Requirement: Nodes without evidence fall back per node
A node with no usable green head within the lookup bounds SHALL be treated as never passed and MUST fall back to the merge-base diff. It MUST run only if a path it owns changed against the merge base, so one node lacking evidence does not select the others.

#### Scenario: Unrelated node never ran
- **WHEN** the first run was Android-only, so `web-extension` has no evidence, and the next push changes only `android/`
- **THEN** `web-extension` is evaluated against the merge base, finds no owned changes and is skipped

#### Scenario: Previous run failed
- **WHEN** the only earlier run for a node failed and the next push changes only documentation
- **THEN** the node is evaluated against the merge base and runs if the pull request changed any path it owns

### Requirement: Evidence lookup is bounded and fails closed
The lookup SHALL inspect at most 20 candidate heads and make at most 100 API calls by default, both as named configurable constants. Any API error, rate limit, malformed response or missing permission MUST select every node, emit a warning annotation and leave ownership validation in force.

#### Scenario: API outage
- **WHEN** the Actions API returns an error while reading runs
- **THEN** every node is selected and the log carries a warning

#### Scenario: Candidate bound reached
- **WHEN** no green head is found within 20 candidates
- **THEN** the node is treated as having no usable evidence

#### Scenario: Call bound reached
- **WHEN** the 100-call bound is exhausted before every candidate is judged
- **THEN** the remaining nodes are treated as having no usable evidence

### Requirement: Master pushes always run every node
For a push to `master`, `ci.yml` SHALL select every node without calling the Actions API. Ownership validation MUST still run on the `before..after` changes.

#### Scenario: Docs-only push to master
- **WHEN** a push to `master` changes only `docs/ci.md`
- **THEN** every node of `ci.yml` runs

#### Scenario: Validation still runs
- **WHEN** a push to `master` contains a path that no node, global or unowned entry accounts for
- **THEN** every node runs and the `changes` job fails after writing its outputs

### Requirement: Ownership validation is independent of the selection mode
Whatever the selection mode (first run, last-green, push, or API fallback), the `changes` job SHALL validate ownership on the full set of changed paths and MUST write the node outputs before failing on an unaccounted path, so the selected nodes still run and `ci-ok` fails.

#### Scenario: Unaccounted path during a later run
- **WHEN** a later pull request run contains a changed path that is not owned, global, retired or unowned
- **THEN** the selected nodes run and `ci-ok` fails because `changes` failed

#### Scenario: Deleted path in a move
- **WHEN** a pull request deletes files under a path that moved and that path is listed in `retired-paths`
- **THEN** validation succeeds and the listed nodes are selected

### Requirement: Expected jobs per node are declared and contract-tested
`.github/components.json` SHALL declare, for every node, the display names of the jobs it reports, without the `<node> / ` prefix. The workflow contract suite MUST derive the real names by following each `ci.yml` caller to its reusable workflow (job `name:`, or the job id when unnamed) and MUST fail on any difference, on a matrix job, or on any other shape it cannot derive.

#### Scenario: Unnamed called job
- **WHEN** the `supply-chain` caller invokes a reusable workflow whose job `build` has no `name:`
- **THEN** the derived job name is `build` and it must equal the declared name

#### Scenario: Drift
- **WHEN** a check workflow gains a job that `components.json` does not list
- **THEN** the contract suite fails

#### Scenario: Matrix job
- **WHEN** a check workflow job uses `strategy.matrix`
- **THEN** the contract suite fails as an unsupported shape

### Requirement: Selection is explained in the run
The `changes` job SHALL write a step summary listing, for every node, whether it runs, the evidence head used or `none`, and the reason (`never passed`, `incompatible map`, `changed since <sha>`, `unchanged since <sha>`, `fallback`).

#### Scenario: Skipped by evidence
- **WHEN** `android` is skipped because nothing it owns changed since a green head
- **THEN** the summary names that head and the reason `unchanged since <sha>`

### Requirement: Last-green selection can be disabled
Setting the repository variable `CI_LAST_GREEN` to `off` SHALL make later pull request runs behave like a first run for every node.

#### Scenario: Kill switch
- **WHEN** `CI_LAST_GREEN` is `off` and a pull request receives a second push
- **THEN** every node is evaluated against the merge base

### Requirement: Evidence lookup uses read-only permissions
The `changes` job SHALL declare `contents: read`, `actions: read` and `pull-requests: read` and MUST NOT use secrets, so that pull requests from forks can use the lookup.

#### Scenario: Fork pull request token
- **WHEN** a pull request from a fork triggers `ci.yml`
- **THEN** the `changes` job reads evidence with its read-only token and no secret is exposed
