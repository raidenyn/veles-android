# commit-conventions Specification

## Purpose
TBD - created by archiving change component-releases-and-conventional-commits. Update Purpose after archive.

## Requirements
### Requirement: Single commit message validator
The repository SHALL provide `scripts/commitlint.py`, using only the Python standard library, as the single Conventional Commits validator for the local hook and CI. It MUST accept a message file or string, normalise CRLF line endings, ignore lines starting with `#`, and print the reason for every rejection.

#### Scenario: Same validator everywhere
- **WHEN** the hook and the `pr-title` check validate the same text
- **THEN** both run `scripts/commitlint.py` and reach the same verdict

#### Scenario: Windows line endings and git comments
- **WHEN** the hook receives `fix: a\r\n\r\nbody\r\n# Please enter the commit message\r\n`
- **THEN** the message is accepted

### Requirement: Commit header format
A message SHALL have a header `type(scope)!: subject` where type is one of `feat`, `fix`, `perf`, `refactor`, `docs`, `test`, `build`, `ci`, `chore`, `style`, `revert`. Scope and `!` MUST be optional, a scope MUST be lowercase, the subject MUST be non-empty, and the header MUST be at most 100 characters.

#### Scenario: Valid header
- **WHEN** the validator checks `feat(android): add auto-copy toggle`
- **THEN** it exits 0

#### Scenario: Missing type
- **WHEN** the validator checks `OTP-26: add auto-copy toggle`
- **THEN** it exits non-zero and explains that the header must start with an allowed type

#### Scenario: Header too long
- **WHEN** the header is 101 characters long
- **THEN** it exits non-zero and reports the length

### Requirement: Body and breaking-change footer
A body SHALL be separated from the header by a blank line. A `BREAKING CHANGE:` or `BREAKING-CHANGE:` footer MUST be accompanied by `!` in the header.

#### Scenario: Missing blank line
- **WHEN** the body starts on the line directly after the header
- **THEN** it exits non-zero and asks for a blank line

#### Scenario: Breaking footer without bang
- **WHEN** the header is `feat(protocol): v2 frames` and the body has `BREAKING CHANGE: v1 removed`
- **THEN** it exits non-zero and asks for `feat(protocol)!:`

### Requirement: Git-generated messages in the local hook
The local hook SHALL accept git-generated subjects (`Merge …`, `Revert "…"`, `fixup! …`, `squash! …`); CI checks MUST NOT accept them.

#### Scenario: Fixup commit locally
- **WHEN** a contributor runs `git commit --fixup HEAD`
- **THEN** the hook accepts it, while `pr-title` rejects a title `fixup! feat: a`

### Requirement: Local commit hook without Node
The repository SHALL ship `.githooks/commit-msg`, which runs the validator. The Gradle task `installGitHooks` MUST set `core.hooksPath` to `.githooks` only when it is unset, MUST leave any other configured value unchanged with a warning, MUST do nothing when `CI` is `true`, and MUST run as part of every Android build (`:app:preBuild`). No root `package.json` and no npm lifecycle script MUST be required.

#### Scenario: Android contributor
- **WHEN** a contributor with no `core.hooksPath` runs `./gradlew assembleDebug`
- **THEN** `core.hooksPath` becomes `.githooks` and `git commit -m "wip"` is rejected

#### Scenario: Existing custom hooks
- **WHEN** `core.hooksPath` is already `~/.git-hooks`
- **THEN** `installGitHooks` prints a warning and leaves it unchanged

#### Scenario: Non-Android contributor
- **WHEN** a contributor who never builds Android runs `./gradlew installGitHooks`
- **THEN** the hook is installed

#### Scenario: CI does not install hooks
- **WHEN** a Gradle build runs with `CI=true`
- **THEN** the git configuration is not changed

### Requirement: PR title check
A workflow `pr-title.yml` SHALL validate the PR title and body as the check `pr-title` on `pull_request` types `opened`, `edited`, `reopened` and `synchronize`, without skipping any event. It MUST pass the title and body through environment variables, never by interpolating them into a script, and MUST run the validator from the PR's base commit, falling back to the head copy with a warning only when the base has no validator. No other workflow MUST trigger on `edited`.

#### Scenario: Title fixed after failure
- **WHEN** `Update installer` is renamed to `fix(installer): handle missing registry key`
- **THEN** `pr-title` re-runs and passes and no other workflow starts

#### Scenario: New commit pushed
- **WHEN** a new commit is pushed to a PR with a valid title
- **THEN** `pr-title` reports on the new head commit

#### Scenario: PR edits the validator
- **WHEN** a PR modifies `scripts/commitlint.py` to accept any title
- **THEN** that PR's `pr-title` check uses the base commit's validator

#### Scenario: Body edit with invalid title
- **WHEN** only the body of a PR with an invalid title is edited
- **THEN** `pr-title` re-runs and still fails

#### Scenario: Title with shell syntax
- **WHEN** the title is ``feat: `rm -rf /` $(id)``
- **THEN** the text is validated as data and nothing is executed

### Requirement: Squash commits equal the checked PR title
The repository SHALL allow only squash merges into `master`, with the squash commit title from the PR title and message from the PR body, and a `master` ruleset requiring a pull request, the checks `pr-title` and `ci-ok`, and linear history, blocking force pushes and deletion.

#### Scenario: Single-commit PR
- **WHEN** a PR with one commit `wip` titled `fix(web-extension): debounce reconnect` is squash-merged with the default message
- **THEN** the `master` commit subject is `fix(web-extension): debounce reconnect`

### Requirement: Post-merge commit guard
Every push to `master` SHALL run `commit-guard`, which validates each pushed commit and fails with an annotation naming every non-conforming commit.

#### Scenario: Hand-edited squash title
- **WHEN** a squash merge lands with the subject `misc fixes`
- **THEN** `commit-guard` fails and names that commit, and the release planner ignores it
