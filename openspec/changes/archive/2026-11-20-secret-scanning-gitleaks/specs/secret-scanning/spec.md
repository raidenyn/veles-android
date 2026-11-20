## ADDED Requirements

### Requirement: Pinned gitleaks install
The root build SHALL provide `./gradlew installGitleaks`, which installs the gitleaks version pinned in `scripts/release-tools.toml` into `build/release-tools/gitleaks/` (`gitleaks`, or `gitleaks.exe` on Windows). It MUST extract only the executable and MUST fail unless the installed binary reports the pinned version. Pins MUST exist for Linux x64 and arm64, macOS arm64 and x64, and Windows x64; any other platform MUST fail with a message naming the OS and architecture.

#### Scenario: Install on a supported platform
- **WHEN** a contributor on Linux x64 runs `./gradlew installGitleaks`
- **THEN** `build/release-tools/gitleaks/gitleaks` exists, is executable and reports the pinned version

#### Scenario: Wrong version reported
- **WHEN** the extracted binary reports a version other than the pin
- **THEN** the task fails naming the expected and reported versions

#### Scenario: Unsupported platform
- **WHEN** the task runs on an OS and architecture with no pin
- **THEN** it fails with a message naming that OS and architecture and installs nothing

### Requirement: Digest verification before extraction
`installGitleaks` SHALL verify the downloaded archive's SHA-256 against the per-platform digest pinned in `scripts/release-tools.toml` before extracting anything. On a mismatch it MUST delete the archive and fail naming the expected and actual digests.

#### Scenario: Digest mismatch
- **WHEN** the pinned digest for the host platform does not match the downloaded archive
- **THEN** the task fails naming both digests, deletes the archive and installs nothing

### Requirement: One shared pinned-tool install path
The pinned git-cliff and gitleaks installs SHALL share one implementation of download, digest check before extraction, single-executable extraction and version check. `installGitCliff` MUST keep its task name, its output path `build/release-tools/git-cliff/`, its pins in `scripts/release-tools.toml` and its observable behaviour.

#### Scenario: Release workflows unaffected
- **WHEN** `node --test verify/test/workflow-contracts.test.mjs` runs and a release workflow calls `./gradlew installGitCliff`
- **THEN** the contracts pass and git-cliff installs to the same path with the same version check as before

### Requirement: Pre-commit hook and binary resolution
The repository SHALL ship `.githooks/pre-commit`, a POSIX `sh` script active wherever `installGitHooks` has set `core.hooksPath=.githooks`. It MUST locate the repository root with `git rev-parse --show-toplevel`. It MUST use only the binary named by `VELES_GITLEAKS` when that variable is set and non-empty, and otherwise `<root>/build/release-tools/gitleaks/gitleaks` or `gitleaks.exe`.

#### Scenario: Scanner not installed
- **WHEN** the gitleaks binary is absent or not executable at the resolved path
- **THEN** the hook prints a warning naming `./gradlew installGitleaks` and exits 0

#### Scenario: Override names a missing binary
- **WHEN** `VELES_GITLEAKS` names a path that does not exist and an executable that would report a leak sits at `build/release-tools/gitleaks/gitleaks` in the repository
- **THEN** the hook warns as for a missing binary, exits 0 and does not run the installed one

### Requirement: Staged-change scan outcomes
The hook SHALL scan only the staged changes, with the repository's `.gitleaks.toml` when it exists. The scan MUST redact secret values, MUST name each finding's rule and file in the commit output, and MUST use a dedicated leak exit code distinct from the scanner's own error codes. Only the leak exit code MUST block the commit.

#### Scenario: Staged secret refused
- **WHEN** a contributor stages a file containing a value the scan rules flag and runs `git commit`
- **THEN** the commit is refused, the output names the rule and the file, explains how to remove or allowlist it and that `git commit --no-verify` bypasses the check, and does not contain the value

#### Scenario: Clean commit passes
- **WHEN** the staged changes contain nothing the scan rules flag
- **THEN** the hook exits 0 and the commit proceeds

#### Scenario: Scanner error
- **WHEN** the scanner exits with a non-zero code other than the leak exit code
- **THEN** the hook prints a warning that the scan did not run, naming the exit code, and exits 0

### Requirement: Scan configuration and allowlist policy
The repository SHALL ship `.gitleaks.toml`, which extends the scanner's default rules. Every allowlist entry MUST say what the flagged text is and why it is not a secret. A path-only entry (one that matches by path alone, with no `regexes` or `condition`) MUST cover only tracked test data that a full-history scan flagged, MUST be the narrowest path that covers it, and MUST NOT cover a file whose flagged content is prose, source logic or harness code. Test data means fixture, conformance-vector or recorded-evidence content, including such content inside a test source file (for example `rust/veles-crypto/tests/protocol_fixtures.rs`). A tracked false positive in any other file MUST be allowlisted only by a line-scoped entry that requires both that file's exact path and a regex for the benign token (`condition = "AND"`, `regexTarget = "line"`); the regex MUST match only that benign token — a literal or anchored expression, never a wildcard covering the line or the file — so a flagged secret on any other line of the same file is still detected. History-only false positives MUST be suppressed by fingerprint in `.gitleaksignore`. A real secret MUST never be allowlisted or suppressed. Reviewers check the test-data classification and each entry's reason; the line-scope guarantee is checked by the apply-time negative control, since the classification itself is a judgement no test can settle.

#### Scenario: Full-history scan is clean after triage
- **WHEN** the pinned gitleaks scans the full git history with `--redact` and the repository config
- **THEN** it reports no finding

#### Scenario: Allowlist entries are justified
- **WHEN** a reviewer reads `.gitleaks.toml`
- **THEN** every allowlist entry names either a test-data path or the line-scoped benign token in its named file, and states why its content is not a secret

#### Scenario: A non-test-data false positive is line-scoped
- **WHEN** a tracked non-test-data file has one benign line that the rules flag
- **THEN** its allowlist entry requires both the exact path and a line regex for the benign token, and a different flagged line in the same file still blocks a commit

#### Scenario: A line-scoped regex is specific
- **WHEN** a reviewer reads a line-scoped allowlist entry
- **THEN** its regex matches only the benign token (a literal or anchored expression), not a wildcard covering the line or the file

### Requirement: Offline hook tests
`scripts/tests/test_pre_commit_hook.py` SHALL test the hook with the standard library only, without network access, sleeps or the wall clock, in temporary git repositories with an isolated `HOME` and `PATH`. It MUST cover a refused staged secret, a clean staged file, a missing binary and a scanner error. Cases that need the real binary MUST skip individually when it is unavailable.

#### Scenario: Binary unavailable
- **WHEN** the tests run without `VELES_GITLEAKS` pointing at a real binary
- **THEN** the refused and clean cases skip, and the missing-binary and scanner-error cases still run (the scanner-error case always skips on Windows, where the stub cannot run directly)

### Requirement: Refused-case fixture
The refused case SHALL stage an AWS-style access key ID that is assembled at runtime, so no literal key is tracked, and that does not end in `EXAMPLE`. The test MUST assert that the ID appears in neither stdout nor stderr.

#### Scenario: No key leaks into output
- **WHEN** the refused case runs against the pinned binary
- **THEN** the hook exits non-zero, the output names the rule and the staged file, and the key ID is absent from both streams

### Requirement: Hook tests run in CI
`./gradlew scriptsTest` SHALL install the pinned gitleaks first and pass it to the tests through `VELES_GITLEAKS`.

#### Scenario: CI runs the hook tests
- **WHEN** `./gradlew scriptsTest` runs in the `tooling` node's CI job (Ubuntu)
- **THEN** all four hook cases run and pass, the refused and clean cases against the pinned binary

### Requirement: Recorded enforcement model
`docs/ci.md` SHALL describe the pre-commit hook and the enforcement model, and MUST list CODEOWNERS coverage of the scanner config as a follow-up, plus an always-run CI scan job whenever the chosen model has none. With push protection enabled, the model is push protection plus the hook, with no CI scan job.

#### Scenario: Reader finds the decision
- **WHEN** a reader opens the "Git hooks" section of `docs/ci.md` after the owner enabled push protection
- **THEN** it describes the pre-commit hook, push protection plus the hook as the enforcement, and both follow-ups

### Requirement: Enforcement model follows the owner's answer
The enforcement model SHALL match the push-protection state the owner reports. If the owner declines, the no-CI-job decision MUST go back to the user before the PR leaves draft, a chosen CI scan job MUST first be added to this change's plan, and the PR description MUST record the owner's answer and the user's decision.

#### Scenario: Owner declines push protection
- **WHEN** the owner reports that secret scanning or push protection stays disabled
- **THEN** the PR description records the owner's answer and the user's renewed decision on a CI scan job before the PR leaves draft, and `docs/ci.md` describes the model the user chose

### Requirement: Contributor guidance and component ownership
`CONTRIBUTING.md` SHALL tell contributors to run `./gradlew installGitleaks` once per clone or worktree, and `.agents/rules/repo-signing-keys.md` MUST point at the scanner in one line, `scripts/AGENTS.md` MUST name the pinned gitleaks and `installGitleaks` next to git-cliff, and `docs/ci.md` MUST note that `scriptsTest` also downloads gitleaks. `.gitleaks.toml` and `.gitleaksignore` (listed even before the file exists) MUST belong to the `tooling` node in `.github/components.json`.

#### Scenario: Component map covers the scanner files
- **WHEN** `python3 -I scripts/components.py check` runs
- **THEN** it passes with `.gitleaks.toml` and `.gitleaksignore` owned by `tooling`

#### Scenario: Contributor finds the setup
- **WHEN** a contributor reads the "Git Hooks" section of `CONTRIBUTING.md`
- **THEN** it names `./gradlew installGitleaks` and says the pre-commit scan only warns until it is installed
