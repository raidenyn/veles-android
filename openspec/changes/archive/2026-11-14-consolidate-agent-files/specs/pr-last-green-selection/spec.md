## MODIFIED Requirements

### Requirement: Later PR runs select each node against its last green head
For every `ci.yml` run of a pull request after the first, the `changes` job SHALL decide per node. When a green evidence head exists for the node, the node MUST be selected only if a path it owns changed between that evidence head and the pull request head (`github.event.pull_request.head.sha`). The node is the unit: when selected, all of its jobs MUST run.

#### Scenario: Android passed, docs pushed
- **WHEN** `android` was green on the previous pull request head and the next push changes only `docs/ci.md`
- **THEN** `android` is skipped and `docs` is selected

#### Scenario: Android passed, web extension pushed
- **WHEN** `android` was green on the previous head and the next push changes only `web-extension/src/`
- **THEN** `android` is skipped and `web-extension` is selected

#### Scenario: Android passed, shared Rust pushed
- **WHEN** `android` was green on the previous head and the next push changes `rust/veles-crypto/src/lib.rs`
- **THEN** `android` is selected and all of its jobs run

#### Scenario: Master movement is ignored
- **WHEN** `master` advanced with changes to `android/` after the evidence head and the pull request itself did not touch `android/`
- **THEN** `android` is skipped
