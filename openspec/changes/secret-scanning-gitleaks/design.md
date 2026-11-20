## Context

Nothing scans commits for secrets. Signing material is git-ignored (`android/config/android_keys/`) and read
from `VELES_KEYSTORE_*` variables, and no key-like file is tracked; the danger is a future mistake, and a
published signing key is irreversible. The repository is public, so GitHub secret scanning and push
protection cost nothing, but the agent's token has no admin rights (`permissions.admin` is `false`,
`security_and_analysis` reads back `null`), so only the owner can check or enable them.

What exists to build on:

- `.githooks/commit-msg`, made active by `./gradlew installGitHooks` (`.githooks/install-hooks.gradle.kts`),
  which sets `core.hooksPath=.githooks` only when it is unset and does nothing under `CI=true`. Any new file in
  `.githooks/` is active wherever `commit-msg` is.
- `InstallGitCliffTask` in `build.gradle.kts`: downloads one archive, checks its SHA-256 against
  `scripts/release-tools.toml` before extracting, extracts only the executable into
  `build/release-tools/git-cliff/`, and checks `--version`. `scriptsTest` depends on it and passes the binary
  in `VELES_GIT_CLIFF`. `release-build.yml` and `publish-release.yml` call `installGitCliff`, and
  `verify/test/workflow-contracts.test.mjs` asserts they do.
- `check-tooling.yml` runs `./gradlew scriptsTest` on Ubuntu whenever a `tooling` path changes.
- `scripts/tests/test_commit_msg_hook.py` shows how a hook is tested: a temporary directory, a minimal `PATH`,
  `subprocess` against the hook script.

Test data that looks secret-like: SPAKE2 vectors and hex keys under `protocol/` conformance fixtures,
`rust/veles-crypto/tests/protocol_fixtures.rs`, and `native-bridge/feasibility/evidence/**`.

## Goals / Non-Goals

**Goals:**

- A staged secret is refused locally before it enters history, with no secret value printed.
- The scanner binary is pinned and digest-verified exactly like git-cliff.
- A contributor without the binary, or with a broken one, is warned and never blocked.
- The hook's outcomes are covered by offline tests that run in CI.
- The enforcement model (push protection plus the hook, no CI job) is written down.

**Non-Goals:**

- A CI secret-scanning job (issue option (b)); it stays a documented follow-up.
- SAST (Semgrep or similar).
- Rotating, regenerating or moving any key.
- Changing `installGitHooks`, `commit-msg` or the CI workflow tree.
- Adding the scanner config to CODEOWNERS (a documented follow-up).

## Decisions

### D1. Enforcement: push protection plus a local hook, no CI job

Push protection blocks a recognised secret at the GitHub boundary for pushes from any machine; the hook catches
it earlier, before local history needs rewriting. A CI job would change the path-selective CI tree and its
contract tests and is redundant with push protection for the dangerous case. The decision assumes the owner
enables push protection (D9); if the owner declines, it goes back to the user before the PR leaves draft. Alternatives considered: the
`pre-commit` framework (a second hook mechanism that takes over `core.hooksPath`, no repository-format digest
pins) and push protection alone (no local layer). Both rejected in the brainstorm.

### D2. One shared pinned-tool install task

`InstallGitCliffTask` becomes a general `InstallPinnedToolTask` with these inputs: `toolName`, `version`,
`downloadUrl`, `archiveName`, `archiveEntry` (path of the executable inside the archive), `binaryName`,
`sha256`, `versionArgs` and `expectedVersionOutput`, plus the existing `installDir` and `downloadDir`. The
download, SHA-256-before-extract check, delete-on-mismatch, single-entry extraction, executable bit and
version check stay one code path. A zip is chosen by an archive name ending in `.zip`.

A single resolver maps the JVM `os.name`/`os.arch` to an (os, arch) pair; each tool maps the pair to its own
platform key: git-cliff keeps its Rust triples (`x86_64-unknown-linux-gnu`, …), gitleaks uses its asset names
(`linux_x64`, `linux_arm64`, `darwin_arm64`, `darwin_x64`, `windows_x64`). An unsupported platform fails the
install task with a message naming the OS and architecture.

`installGitCliff` keeps its task name, description, output directory and observable behaviour.
`installGitleaks` installs to `build/release-tools/gitleaks/gitleaks[.exe]`. Download URL:
`https://github.com/gitleaks/gitleaks/releases/download/v<ver>/gitleaks_<ver>_<key>.tar.gz` (`.zip` for
Windows). The archive entry and the version command output are confirmed against the pinned release at apply
(the archive is expected to hold `gitleaks` at its root; `gitleaks version` is expected to print the bare
version); apply records both observed values in the task notes.

Why over copying the class: one verify-before-extract implementation to audit, and the mismatch branch that
`installGitCliff` already exercises is the same one `installGitleaks` uses.

### D3. Pins in `scripts/release-tools.toml`

```toml
gitleaks = "8.30.1"
gitleaks-linux_x64 = "<sha256>"
gitleaks-linux_arm64 = "<sha256>"
gitleaks-darwin_arm64 = "<sha256>"
gitleaks-darwin_x64 = "<sha256>"
gitleaks-windows_x64 = "<sha256>"
```

Each digest is cross-checked against the GitHub release asset digest API and the release's
`gitleaks_8.30.1_checksums.txt`; the header comment is reworded to cover both tools and each tool's
cross-check source. v8.30.1 was published 2026-03-21, well past any freshness window.

### D4. The hook: `.githooks/pre-commit`

POSIX `sh`, like `commit-msg` (Git for Windows runs it with its bundled `sh`):

1. `root=$(git rev-parse --show-toplevel)`.
2. Binary: when `VELES_GITLEAKS` is set and non-empty, that path only (no fallback); otherwise
   `$root/build/release-tools/gitleaks/gitleaks`, else the same with `.exe`. Not executable or absent → print
   a warning to stderr naming `./gradlew installGitleaks`, exit 0. The hook uses only shell builtins besides
   `git` and the gitleaks binary.
3. Run the pinned binary's staged-changes scan (the exact subcommand and flags are read from the pinned
   binary's `--help` at apply; expected `gitleaks git --staged`) with `--redact`, `--verbose` (so the report
   names the rule, file and line, still redacted), `--no-banner`, a dedicated leak exit code via
   `--exit-code <N>`, and `--config "$root/.gitleaks.toml"` when that file exists. gitleaks prints the
   per-finding report on stdout and its summary on stderr (confirmed on the pinned binary at apply); the hook
   sends both to stderr (`1>&2`) so the
   commit output shows them together. `N` is a value gitleaks does not use for its own errors (it exits 1 on
   fatal errors and 126 on an unknown flag), fixed at apply; the design expects `N = 3`. The scan runs with the
   working directory at `$root`.
4. Exit `N` → print a block message (a secret-looking value is staged; remove it, or, if it is a false
   positive, allowlist it in `.gitleaks.toml` with a reason; `git commit --no-verify` is the deliberate
   bypass) and exit 1. Exit 0 → exit 0. Any other code → print a warning naming the code and that the scan did
   not run, exit 0.

Why a tool error does not block: the issue defines the hook as a convenience layer; the tests catch a broken
config or flag before it ships, and push protection remains the enforcement.

### D5. Scan configuration

`.gitleaks.toml`:

```toml
[extend]
useDefault = true

[[allowlists]]
description = "<what the data is and why it is not a secret>"
paths = ['''<narrowest regex covering the flagged test data>''']
```

Path allowlists are added only for paths the full-history scan (D6, `gitleaks git`) flags as test data in
files that are still tracked. The three issue
paths are the expected ones; any other flagged test-data path is allowlisted the same way, narrowed to the
smallest path, and listed in the PR. The allowlist syntax (`[[allowlists]]` versus the older
`[allowlist]`) is the one the pinned version documents. History-only false positives (paths no longer in the
tree) go to `.gitleaksignore` by fingerprint; fingerprints are commit-bound and gitleaks also reads the file in
staged scans, which is harmless because a commit-bound fingerprint never matches a staged finding.

### D6. Full-history scan first

Before the config is final, run the pinned binary over the whole history with `--redact` and triage every
finding as test data, history-only false positive, or real secret. A real secret stops the work: report it
(path and rule only, never the value) to the owner for rotation; never allowlist it. Record counts only
(total, per category) in the PR. After the config lands, a repeat full scan reports no finding.

### D7. Tests: `scripts/tests/test_pre_commit_hook.py`

Stdlib `unittest`, no network, no sleep, no clock. Each test creates a temporary git repository
(`git init`, local `user.name`/`user.email`), copies the repository's `.gitleaks.toml` into it, stages a
file, and runs the project's hook by absolute path (`sh <project>/.githooks/pre-commit`, as `HOOK` in
`test_commit_msg_hook.py`) with the temporary repository as the working directory. The environment is
isolated: `HOME` is the temporary directory, `GIT_CONFIG_NOSYSTEM=1`, `PATH` holds only what the hook needs
(a symlink to `git`, as in the existing hook test; the hook needs nothing else), and `VELES_GITLEAKS` is set
per case. Cases:

| Case | `VELES_GITLEAKS` | Staged | Expect |
|---|---|---|---|
| refused | real binary | an AWS access key ID built at runtime | exit non-zero; the combined output names the rule ID and the staged file and contains `--no-verify`; the key text is in neither stdout nor stderr |
| clean | real binary | ordinary text | exit 0 |
| missing | a path that does not exist, with an executable stub (executable bit set and checked) that exits with the leak code placed at `<tmp repo>/build/release-tools/gitleaks/gitleaks` | anything | exit 0; stderr names `./gradlew installGitleaks` (proves there is no fallback) |
| tool error | an executable stub script (`#!/bin/sh`, `exit 2`) | anything | exit 0; stderr warns that the scan did not run |

The fake key is an AWS access key ID alone on its own line (`aws_access_key_id = <id>`), assembled from
string pieces at runtime (so no literal key is in the repository). It does not end in `EXAMPLE` (the default
rule ignores those) and has a random-looking base32 body that clears the rule's entropy floor; the expected
rule is the default `aws-access-token`, and that it fires is confirmed at apply. No secret-access-key line is
staged, so the only sensitive string is the ID, which must appear in neither stdout nor stderr. Every case needs `sh` and `git`;
only refused and clean need the real binary, and skip per test without it. On Windows the tool-error
case always skips (the hook runs `$VELES_GITLEAKS` directly, and CI runs on Ubuntu).

`scriptsTest` depends on `installGitleaks` and sets `VELES_GITLEAKS` to the installed binary for the test run.

### D8. Docs, rule and component map

- `docs/ci.md` "Git hooks": the `pre-commit` hook, how to install the binary, the four outcomes, the
  enforcement decision (D1) with option (b) and CODEOWNERS for the scanner config as follow-ups, and that
  `scriptsTest` now also downloads gitleaks.
- `CONTRIBUTING.md` "Git Hooks": `./gradlew installGitleaks`, once per clone or worktree, and that the
  pre-commit scan only warns until it is installed.
- `scripts/AGENTS.md`: the pinned gitleaks next to git-cliff in the overview and the Build & Test block,
  staying within the 40-line cap.
- `.agents/rules/repo-signing-keys.md`: one line pointing at the pre-commit scanner and push protection.
- `build.gradle.kts`: the release-tooling section comment and the `scriptsTest` description name both pinned
  tools.
- `.github/components.json`: `.gitleaks.toml` and `.gitleaksignore` added to the `tooling` node's paths;
  `python3 -I scripts/components.py check` passes.

### D9. Owner action and recording

The owner checks Settings → Code security, enables secret scanning and push protection or declines, and tells
the agent; the agent records the answer in the PR description. If the owner declines, the no-CI-job decision
(D1) goes back to the user before the PR leaves draft, and the PR description records that decision too;
`docs/ci.md` then describes the model the user chose, and lists the CI-job follow-up only if it has none. A
CI scan job the user chooses is first added to this change's plan (and its planning files re-reviewed) before
it is built. The work is untracked, so nothing is written to
the issue; the PR body carries a non-closing reference to it.

## Risks / Trade-offs

- [The hook is opt-in: a clone without `installGitleaks` is unprotected locally] → push protection covers the
  push; the warning on every commit names the fix.
- [A tool error lets commits through] → intended (D4); the tool-error case covers the branch, and a broken
  `.gitleaks.toml` or flag makes the refused case fail (gitleaks exits 1, the hook only warns, the test expects
  a block), so CI catches it; push protection remains.
- [A false positive in new test data blocks a commit] → the block message explains the allowlist path with a
  reason; `--no-verify` is the deliberate bypass.
- [Allowlist widening goes unnoticed in review] → each entry carries a reason; CODEOWNERS coverage is a
  documented follow-up.
- [`scriptsTest` now needs a second download, so a GitHub outage or a wrong pin fails it] → same failure mode
  as git-cliff today; the digest check makes a wrong pin loud rather than silent.
- [The CLI of the pinned version differs from what is expected] → apply reads `--help` of the pinned binary;
  tests exercise the real binary.
- [The wrong-digest check is a one-off manual run, not a repeatable test] → the mismatch branch is shared code
  already used by `installGitCliff`; the manual run is recorded.
- [`.gitleaksignore` fingerprints go stale after a history rewrite] → the history is not rewritten; the hook
  never depends on them.

## Migration Plan

No migration. Contributors run `./gradlew installGitleaks` once per clone or worktree; until they do, the hook
warns and passes. Rollback is deleting `.githooks/pre-commit`; the shared install task stays harmless.

## Open Questions

- The owner's answer on secret scanning and push protection — needed before the PR leaves draft.
- Whether to file the CI-job and CODEOWNERS follow-ups as issues.
