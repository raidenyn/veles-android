## Why

Nothing scans commits for secrets. Signing keys and keystore passwords are git-ignored and none is tracked
today, so the risk is a future mistake, but a published signing key cannot be taken back. The repository is
public, so GitHub's secret scanning and push protection are free, and the repository already has the two
patterns a local scanner needs: a hooks directory and SHA-256-pinned tool installs. A cheap, layered safety net
now costs little and closes an irreversible failure mode.

## What Changes

- **GitHub secret scanning and push protection** (owner action): the owner checks both in the repository
  settings and enables them or declines; the agent cannot read or change them and records the owner's answer in the PR.
- **`./gradlew installGitleaks`**: installs gitleaks v8.30.1 into `build/release-tools/gitleaks/`, verifying a
  per-platform SHA-256 pinned in `scripts/release-tools.toml` before extraction. The existing git-cliff install
  task is generalised into one shared pinned-tool task; `installGitCliff` keeps its name, output and behaviour.
- **`.githooks/pre-commit`**: scans the staged changes with the pinned gitleaks and the repository config.
  - From: commits are never scanned.
  - To: a finding blocks the commit with a redacted report; a missing binary or a tool error only warns.
  - Reason: catch a secret before it enters local history.
  - Impact: non-breaking; active wherever `installGitHooks` already set `core.hooksPath=.githooks`.
- **`.gitleaks.toml`** (default rules extended, test-data paths allowlisted with reasons) and, if the
  full-history scan needs it, **`.gitleaksignore`** for history-only false positives. A full-history scan runs
  first; a real secret stops the work for owner rotation. Only finding counts are recorded, never values.
- **`scripts/tests/test_pre_commit_hook.py`**: offline tests of the hook's four outcomes; `scriptsTest`
  installs gitleaks and passes it to the tests.
- **Docs**: `docs/ci.md` (hooks, the CI decision, and that `scriptsTest` now also downloads gitleaks), `CONTRIBUTING.md`, `scripts/AGENTS.md`,
  `.agents/rules/repo-signing-keys.md`.
- **Component map**: `.gitleaks.toml` and `.gitleaksignore` join the `tooling` node.
- **CI decision**: no new CI job; push protection plus the hook is the enforcement, recorded in `docs/ci.md`.

## Capabilities

### New Capabilities
- `secret-scanning`: the pinned gitleaks install, the pre-commit hook's outcomes, the repository scan config
  and its allowlist policy, the tests, and the recorded enforcement model (push protection plus the hook, no
  CI job).

### Modified Capabilities
None. `commit-conventions` keeps its `commit-msg` and `installGitHooks` requirements unchanged; the new hook
lives in the same directory and needs no installer change.

## Impact

- Code: `build.gradle.kts` (shared install task, `installGitleaks`, `scriptsTest` dependency),
  `scripts/release-tools.toml`, `.githooks/pre-commit`, `.gitleaks.toml`, optional `.gitleaksignore`,
  `scripts/tests/test_pre_commit_hook.py`, `.github/components.json`.
- Docs: `docs/ci.md`, `CONTRIBUTING.md`, `scripts/AGENTS.md`, `.agents/rules/repo-signing-keys.md`.
- CI: `check-tooling` now downloads the pinned gitleaks through `scriptsTest`; no workflow file changes. A
  failed download or a wrong pin therefore fails `scriptsTest` wherever it runs, as git-cliff already does.
- Dependencies: gitleaks v8.30.1 binary (download only, digest-pinned); no new language packages.
- Contributors: each clone or worktree runs `./gradlew installGitleaks` once to activate scanning.
- Not affected: the Android app, the web extension, the Rust core, the protocol and the native bridge.
